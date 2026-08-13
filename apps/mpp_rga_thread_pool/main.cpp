#include <atomic>
#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include <unistd.h>

#include <opencv2/core.hpp>

extern "C" {
#include <libavformat/avformat.h>
}

#include <rockchip/mpp_buffer.h>
#include <rockchip/mpp_frame.h>
#include <rockchip/mpp_packet.h>
#include <rockchip/rk_mpi.h>

#include "draw/cv_draw.h"
#include "im2d.h"
#include "rga.h"
#include "utils/logging.h"
#include "yolov5/yolov5_thread_pool.h"

namespace {

constexpr size_t kPacketBufferSize = 256 * 1024;
constexpr int kDrainIdleTries = 3;
constexpr int kDrainMaxFramesPerPacket = 8;

struct MppPacketGuard {
    MppPacket packet{nullptr};
    ~MppPacketGuard()
    {
        if (packet) mpp_packet_deinit(&packet);
    }
};

struct MppFrameGuard {
    MppFrame frame{nullptr};
    ~MppFrameGuard()
    {
        if (frame) mpp_frame_deinit(&frame);
    }
};

struct MppDecoder {
    MppCtx ctx{nullptr};
    MppApi *mpi{nullptr};
    MppBufferGroup frame_group{nullptr};

    ~MppDecoder()
    {
        if (ctx) mpp_destroy(ctx);
        if (frame_group) mpp_buffer_group_put(frame_group);
    }
};

struct AvFormatGuard {
    AVFormatContext *ctx{nullptr};
    ~AvFormatGuard()
    {
        if (ctx) avformat_close_input(&ctx);
    }
};

struct AvPacketGuard {
    AVPacket *packet{nullptr};
    ~AvPacketGuard()
    {
        if (packet) av_packet_free(&packet);
    }
};

static MppCodingType parse_coding(const std::string &codec)
{
    if (codec == "h264" || codec == "avc") return MPP_VIDEO_CodingAVC;
    if (codec == "h265" || codec == "hevc") return MPP_VIDEO_CodingHEVC;
    return MPP_VIDEO_CodingUnused;
}

static int rga_format_for_mpp(MppFrameFormat format)
{
    const MppFrameFormat plain = static_cast<MppFrameFormat>(format & MPP_FRAME_FMT_MASK);
    if (plain == MPP_FMT_YUV420SP) return RK_FORMAT_YCbCr_420_SP;
    if (plain == MPP_FMT_YUV420P) return RK_FORMAT_YCbCr_420_P;
    return 0;
}

static bool mpp_buffer_fd(MppBuffer buffer, int *fd)
{
    MppBufferInfo info;
    std::memset(&info, 0, sizeof(info));
    if (mpp_buffer_info_get(buffer, &info) != MPP_OK) return false;
    *fd = info.fd;
    return *fd >= 0;
}

static bool frame_has_dmabuf(MppFrame frame)
{
    MppBuffer buffer = mpp_frame_get_buffer(frame);
    int fd = -1;
    return buffer && mpp_buffer_fd(buffer, &fd);
}

static bool frame_to_model_rgb(MppFrame frame, cv::Mat &model_rgb,
                               LetterBoxInfo &letterbox_info,
                               int target_width, int target_height,
                               double *rga_ms)
{
    const RK_U32 width = mpp_frame_get_width(frame);
    const RK_U32 height = mpp_frame_get_height(frame);
    const RK_U32 h_stride = mpp_frame_get_hor_stride(frame);
    const RK_U32 v_stride = mpp_frame_get_ver_stride(frame);
    const MppFrameFormat fmt = mpp_frame_get_fmt(frame);
    if (MPP_FRAME_FMT_IS_FBC(fmt)) {
        NN_LOG_ERROR("FBC MPP frame is not supported by this path yet");
        return false;
    }
    const int src_format = rga_format_for_mpp(fmt);
    if (src_format == 0) {
        NN_LOG_ERROR("Unsupported MPP frame format: 0x%x", fmt);
        return false;
    }
    MppBuffer src_buffer = mpp_frame_get_buffer(frame);
    int src_fd = -1;
    if (!src_buffer || !mpp_buffer_fd(src_buffer, &src_fd)) {
        NN_LOG_INFO("Skipping MPP frame without dma-buf fd");
        return false;
    }

    const float scale_w = static_cast<float>(target_width) / static_cast<float>(width);
    const float scale_h = static_cast<float>(target_height) / static_cast<float>(height);
    letterbox_info.scale = std::min(scale_w, scale_h);
    const int resized_width = std::max(1, static_cast<int>(std::round(width * letterbox_info.scale)));
    const int resized_height = std::max(1, static_cast<int>(std::round(height * letterbox_info.scale)));
    letterbox_info.pad_x = (target_width - resized_width) / 2;
    letterbox_info.pad_y = (target_height - resized_height) / 2;
    letterbox_info.hor = letterbox_info.pad_x > 0;
    letterbox_info.pad = letterbox_info.hor ? letterbox_info.pad_x : letterbox_info.pad_y;
    letterbox_info.original_width = static_cast<int>(width);
    letterbox_info.original_height = static_cast<int>(height);

    model_rgb.create(target_height, target_width, CV_8UC3);
    model_rgb.setTo(cv::Scalar(0, 0, 0));
    cv::Mat resized_rgb(resized_height, resized_width, CV_8UC3);
    rga_buffer_t src = wrapbuffer_fd(src_fd, width, height, src_format,
                                    static_cast<int>(h_stride), static_cast<int>(v_stride));
    rga_buffer_t dst = wrapbuffer_virtualaddr(resized_rgb.data, resized_width, resized_height,
                                             RK_FORMAT_RGB_888,
                                             static_cast<int>(resized_rgb.step[0] / 3),
                                             resized_height);
    im_rect src_rect;
    im_rect dst_rect;
    std::memset(&src_rect, 0, sizeof(src_rect));
    std::memset(&dst_rect, 0, sizeof(dst_rect));
    int check = imcheck(src, dst, src_rect, dst_rect);
    if (check != IM_STATUS_NOERROR) {
        NN_LOG_ERROR("RGA imcheck failed: %s", imStrError(static_cast<IM_STATUS>(check)));
        return false;
    }

    const auto start = std::chrono::steady_clock::now();
    IM_STATUS status = imcvtcolor(src, dst, src_format, RK_FORMAT_RGB_888,
                                  IM_YUV_TO_RGB_BT601_LIMIT);
    *rga_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    if (status != IM_STATUS_SUCCESS) {
        NN_LOG_ERROR("RGA imcvtcolor failed: %s", imStrError(status));
        return false;
    }
    resized_rgb.copyTo(model_rgb(cv::Rect(letterbox_info.pad_x, letterbox_info.pad_y,
                                          resized_width, resized_height)));
    return true;
}

static bool handle_info_change(MppDecoder &decoder, MppFrame frame)
{
    RK_U32 width = mpp_frame_get_width(frame);
    RK_U32 height = mpp_frame_get_height(frame);
    RK_U32 h_stride = mpp_frame_get_hor_stride(frame);
    RK_U32 v_stride = mpp_frame_get_ver_stride(frame);
    RK_U32 buf_size = mpp_frame_get_buf_size(frame);
    NN_LOG_INFO("MPP info change width=%u height=%u stride=%u:%u buf_size=%u",
                width, height, h_stride, v_stride, buf_size);
    if (!decoder.frame_group) {
        MPP_RET ret = mpp_buffer_group_get_internal(&decoder.frame_group, MPP_BUFFER_TYPE_DRM);
        if (ret == MPP_OK) {
            ret = decoder.mpi->control(decoder.ctx, MPP_DEC_SET_EXT_BUF_GROUP,
                                       decoder.frame_group);
        }
        if (ret != MPP_OK) return false;
    }
    decoder.mpi->control(decoder.ctx, MPP_DEC_SET_INFO_CHANGE_READY, nullptr);
    return true;
}

static bool collect_one_result(Yolov5ThreadPool &pool, int &completed, int &inference_errors,
                               bool draw, double &total_collect_ms)
{
    const auto collect_start = std::chrono::steady_clock::now();
    InferenceResult result;
    nn_error_e nn_ret = pool.getResult(result, completed);
    total_collect_ms += std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - collect_start).count();
    if (nn_ret != NN_SUCCESS) return false;
    if (result.status != NN_SUCCESS) ++inference_errors;
    if (draw) DrawDetections(result.frame, result.detections);
    ++completed;
    return true;
}

} // namespace

int main(int argc, char **argv)
{
    if (argc < 4 || argc > 7) {
        NN_LOG_ERROR("Usage: %s <model.rknn> <annexb.h264|annexb.h265> <h264|h265> [contexts=3] [draw=0|1] [max_frames=0]", argv[0]);
        return 2;
    }

    std::string model_path = argv[1];
    const char *input = argv[2];
    MppCodingType coding = parse_coding(argv[3]);
    const int contexts = argc > 4 ? std::atoi(argv[4]) : 3;
    const bool draw = argc > 5 ? std::atoi(argv[5]) != 0 : true;
    const int max_frames = argc > 6 ? std::atoi(argv[6]) : 0;
    if (coding == MPP_VIDEO_CodingUnused || contexts < 1 || contexts > 20) {
        NN_LOG_ERROR("Invalid codec or contexts; contexts must be between 1 and 20");
        return 2;
    }

    avformat_network_init();
    AvFormatGuard format;
    if (avformat_open_input(&format.ctx, input, nullptr, nullptr) < 0 ||
        avformat_find_stream_info(format.ctx, nullptr) < 0) {
        NN_LOG_ERROR("Failed to open input: %s", input);
        return 3;
    }
    int video_stream = -1;
    for (unsigned i = 0; i < format.ctx->nb_streams; ++i) {
        if (format.ctx->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) {
            video_stream = static_cast<int>(i);
            break;
        }
    }
    if (video_stream < 0) {
        NN_LOG_ERROR("No video stream in input: %s", input);
        return 3;
    }
    AvPacketGuard av_packet;
    av_packet.packet = av_packet_alloc();
    if (!av_packet.packet) {
        NN_LOG_ERROR("Failed to allocate AVPacket");
        return 3;
    }

    MppDecoder decoder;
    MPP_RET ret = mpp_create(&decoder.ctx, &decoder.mpi);
    if (ret != MPP_OK || mpp_init(decoder.ctx, MPP_CTX_DEC, coding) != MPP_OK) {
        NN_LOG_ERROR("Failed to initialize MPP decoder");
        return 4;
    }

    Yolov5ThreadPool pool;
    nn_error_e nn_ret = pool.setUp(model_path, contexts);
    if (nn_ret != NN_SUCCESS) {
        NN_LOG_ERROR("Failed to setup YOLO pool: %d", nn_ret);
        return 5;
    }

    int packets = 0;
    int submitted = 0;
    int completed = 0;
    int decode_errors = 0;
    int inference_errors = 0;
    double total_decode_rga_ms = 0.0;
    double total_put_ms = 0.0;
    double total_get_ms = 0.0;
    double total_submit_ms = 0.0;
    double total_collect_ms = 0.0;
    auto start_all = std::chrono::steady_clock::now();

    while ((max_frames <= 0 || submitted < max_frames) &&
           av_read_frame(format.ctx, av_packet.packet) >= 0) {
        if (av_packet.packet->stream_index != video_stream) {
            av_packet_unref(av_packet.packet);
            continue;
        }
        ++packets;

        MppPacketGuard packet;
        ret = mpp_packet_init(&packet.packet, av_packet.packet->data, av_packet.packet->size);
        if (ret != MPP_OK) {
            ++decode_errors;
            av_packet_unref(av_packet.packet);
            break;
        }
        const auto put_start = std::chrono::steady_clock::now();
        ret = decoder.mpi->decode_put_packet(decoder.ctx, packet.packet);
        total_put_ms += std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - put_start).count();
        if (ret != MPP_OK) {
            ++decode_errors;
            av_packet_unref(av_packet.packet);
            break;
        }

        int idle_tries = 0;
        int frames_from_packet = 0;
        while ((max_frames <= 0 || submitted < max_frames) &&
               idle_tries < kDrainIdleTries &&
               frames_from_packet < kDrainMaxFramesPerPacket) {
            MppFrameGuard frame;
            const auto get_start = std::chrono::steady_clock::now();
            ret = decoder.mpi->decode_get_frame(decoder.ctx, &frame.frame);
            total_get_ms += std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - get_start).count();
            if (ret == MPP_ERR_TIMEOUT) {
                ++idle_tries;
                usleep(1000);
                continue;
            }
            if (ret != MPP_OK) {
                ++decode_errors;
                break;
            }
            if (!frame.frame) {
                ++idle_tries;
                usleep(1000);
                continue;
            }
            idle_tries = 0;
            if (mpp_frame_get_info_change(frame.frame)) {
                if (!handle_info_change(decoder, frame.frame)) ++decode_errors;
                continue;
            }
            if (mpp_frame_get_errinfo(frame.frame) || mpp_frame_get_discard(frame.frame)) {
                ++decode_errors;
                continue;
            }
            if (!frame_has_dmabuf(frame.frame)) {
                NN_LOG_INFO("Skipping MPP frame without dma-buf fd");
                continue;
            }

            const auto decode_start = std::chrono::steady_clock::now();
            cv::Mat model_rgb;
            LetterBoxInfo letterbox_info;
            double rga_ms = 0.0;
            if (!frame_to_model_rgb(frame.frame, model_rgb, letterbox_info, 640, 640, &rga_ms)) {
                ++decode_errors;
                continue;
            }
            const double decode_ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - decode_start).count();
            total_decode_rga_ms += decode_ms;
            const auto submit_start = std::chrono::steady_clock::now();
            pool.submitPreparedRgb(model_rgb, letterbox_info, submitted, decode_ms);
            total_submit_ms += std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - submit_start).count();
            ++submitted;
            ++frames_from_packet;
        }
        av_packet_unref(av_packet.packet);

        while (submitted - completed >= contexts) {
            if (!collect_one_result(pool, completed, inference_errors, draw, total_collect_ms)) break;
        }
    }

    if (max_frames <= 0 || submitted < max_frames) {
        char eos_data = 0;
        MppPacketGuard eos_packet;
        ret = mpp_packet_init(&eos_packet.packet, &eos_data, 0);
        if (ret == MPP_OK) {
            mpp_packet_set_eos(eos_packet.packet);
            decoder.mpi->decode_put_packet(decoder.ctx, eos_packet.packet);
            for (int tries = 0; tries < 50 && (max_frames <= 0 || submitted < max_frames); ++tries) {
                MppFrameGuard frame;
                ret = decoder.mpi->decode_get_frame(decoder.ctx, &frame.frame);
                if (ret != MPP_OK || !frame.frame) {
                    usleep(1000);
                    continue;
                }
                if (mpp_frame_get_info_change(frame.frame)) {
                    handle_info_change(decoder, frame.frame);
                    continue;
                }
                if (mpp_frame_get_errinfo(frame.frame) || mpp_frame_get_discard(frame.frame)) {
                    continue;
                }
                if (!frame_has_dmabuf(frame.frame)) {
                    NN_LOG_INFO("Skipping MPP frame without dma-buf fd");
                    continue;
                }
                cv::Mat model_rgb;
                LetterBoxInfo letterbox_info;
                double rga_ms = 0.0;
                const auto decode_start = std::chrono::steady_clock::now();
                if (!frame_to_model_rgb(frame.frame, model_rgb, letterbox_info, 640, 640, &rga_ms)) continue;
                const double decode_ms = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - decode_start).count();
                total_decode_rga_ms += decode_ms;
                pool.submitPreparedRgb(model_rgb, letterbox_info, submitted, decode_ms);
                ++submitted;
                while (submitted - completed >= contexts) {
                    if (!collect_one_result(pool, completed, inference_errors, draw, total_collect_ms)) break;
                }
                if (mpp_frame_get_eos(frame.frame)) {
                    break;
                }
            }
        }
    }

    while (completed < submitted) {
        if (!collect_one_result(pool, completed, inference_errors, draw, total_collect_ms)) {
            ++inference_errors;
            break;
        }
    }

    const double seconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - start_all).count();
    const double fps = seconds > 0.0 ? completed / seconds : 0.0;
    NN_LOG_INFO("MPP_RGA_POOL_OK packets=%d submitted=%d completed=%d decode_errors=%d inference_errors=%d draw=%d elapsed_s=%.3f fps=%.3f avg_decode_rga_ms=%.3f",
                packets, submitted, completed, decode_errors, inference_errors,
                draw ? 1 : 0, seconds, fps,
                completed > 0 ? total_decode_rga_ms / completed : 0.0);
    NN_LOG_INFO("MPP_RGA_PROFILE avg_put_ms=%.3f avg_get_ms=%.3f avg_submit_ms=%.3f avg_collect_ms=%.3f",
                packets > 0 ? total_put_ms / packets : 0.0,
                completed > 0 ? total_get_ms / completed : 0.0,
                submitted > 0 ? total_submit_ms / submitted : 0.0,
                completed > 0 ? total_collect_ms / completed : 0.0);

    // This short-lived validation app intentionally lets the OS reclaim MPP
    // decoder resources on exit. The board's MPP 1.x userspace can crash in
    // teardown after fd-backed RGA use; production code should replace this
    // with a long-lived decoder and explicit buffer-pool ownership tests.
    decoder.ctx = nullptr;
    decoder.frame_group = nullptr;
    return (decode_errors == 0 && inference_errors == 0 && submitted == completed) ? 0 : 7;
}
