#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>

#include <unistd.h>

#include <rockchip/mpp_buffer.h>
#include <rockchip/mpp_frame.h>
#include <rockchip/mpp_packet.h>
#include <rockchip/rk_mpi.h>

#include "im2d.h"
#include "rga.h"

namespace {

constexpr size_t kPacketBufferSize = 256 * 1024;

struct MppPacketGuard {
    MppPacket packet{nullptr};
    ~MppPacketGuard()
    {
        if (packet) {
            mpp_packet_deinit(&packet);
        }
    }
};

struct MppFrameGuard {
    MppFrame frame{nullptr};
    ~MppFrameGuard()
    {
        if (frame) {
            mpp_frame_deinit(&frame);
        }
    }
};

struct MppBufferGuard {
    MppBuffer buffer{nullptr};
    ~MppBufferGuard()
    {
        if (buffer) {
            mpp_buffer_put(buffer);
        }
    }
};

struct MppContextGuard {
    MppCtx context{nullptr};
    MppApi *mpi{nullptr};
    MppBufferGroup frame_group{nullptr};
    ~MppContextGuard()
    {
        if (context) {
            mpp_destroy(context);
        }
        if (frame_group) {
            mpp_buffer_group_put(frame_group);
        }
    }
};

static MppCodingType parse_coding(const std::string &name)
{
    if (name == "h264" || name == "avc") {
        return MPP_VIDEO_CodingAVC;
    }
    if (name == "h265" || name == "hevc") {
        return MPP_VIDEO_CodingHEVC;
    }
    return MPP_VIDEO_CodingUnused;
}

static int rga_format_for_mpp(MppFrameFormat format)
{
    const MppFrameFormat plain = static_cast<MppFrameFormat>(format & MPP_FRAME_FMT_MASK);
    switch (plain) {
    case MPP_FMT_YUV420SP:
        return RK_FORMAT_YCbCr_420_SP;
    case MPP_FMT_YUV420P:
        return RK_FORMAT_YCbCr_420_P;
    default:
        return 0;
    }
}

static std::string mpp_format_name(MppFrameFormat format)
{
    const MppFrameFormat plain = static_cast<MppFrameFormat>(format & MPP_FRAME_FMT_MASK);
    std::string name = "unknown";
    if (plain == MPP_FMT_YUV420SP) {
        name = "YUV420SP/NV12";
    } else if (plain == MPP_FMT_YUV420P) {
        name = "YUV420P/I420";
    }
    if (MPP_FRAME_FMT_IS_FBC(format)) {
        name += "+FBC";
    }
    return name;
}

static bool query_mpp_buffer_fd(MppBuffer buffer, int *fd)
{
    MppBufferInfo info;
    std::memset(&info, 0, sizeof(info));
    const MPP_RET ret = mpp_buffer_info_get(buffer, &info);
    if (ret != MPP_OK) {
        std::cerr << "mpp_buffer_info_get failed ret=" << ret << "\n";
        return false;
    }
    *fd = info.fd;
    return *fd >= 0;
}

static int run_rga_probe(MppFrame frame, int target_width, int target_height)
{
    const RK_U32 width = mpp_frame_get_width(frame);
    const RK_U32 height = mpp_frame_get_height(frame);
    const RK_U32 h_stride = mpp_frame_get_hor_stride(frame);
    const RK_U32 v_stride = mpp_frame_get_ver_stride(frame);
    const MppFrameFormat frame_format = mpp_frame_get_fmt(frame);
    MppBuffer src_mpp_buffer = mpp_frame_get_buffer(frame);
    if (!src_mpp_buffer) {
        std::cerr << "decoded frame has no MppBuffer\n";
        return 20;
    }

    int src_fd = -1;
    const bool has_fd = query_mpp_buffer_fd(src_mpp_buffer, &src_fd);
    std::cout << "FRAME width=" << width << " height=" << height
              << " h_stride=" << h_stride << " v_stride=" << v_stride
              << " fmt=" << mpp_format_name(frame_format)
              << " dmabuf_fd=" << (has_fd ? std::to_string(src_fd) : "unavailable")
              << "\n";

    const int src_rga_format = rga_format_for_mpp(frame_format);
    if (!has_fd) {
        std::cerr << "MPP buffer did not expose a dma-buf fd\n";
        return 21;
    }
    if (src_rga_format == 0 || MPP_FRAME_FMT_IS_FBC(frame_format)) {
        std::cerr << "RGA probe currently supports non-FBC YUV420SP/YUV420P only\n";
        return 22;
    }

    MppBufferGroup dst_group = nullptr;
    MppBufferGuard dst;
    MPP_RET ret = mpp_buffer_group_get_internal(&dst_group, MPP_BUFFER_TYPE_DRM);
    if (ret != MPP_OK) {
        std::cerr << "failed to create DRM dst buffer group ret=" << ret << "\n";
        return 23;
    }
    const size_t dst_size = static_cast<size_t>(target_width) * target_height * 3;
    ret = mpp_buffer_get(dst_group, &dst.buffer, dst_size);
    mpp_buffer_group_put(dst_group);
    if (ret != MPP_OK) {
        std::cerr << "failed to allocate DRM dst buffer ret=" << ret << "\n";
        return 24;
    }

    int dst_fd = -1;
    if (!query_mpp_buffer_fd(dst.buffer, &dst_fd)) {
        std::cerr << "RGA dst buffer did not expose a dma-buf fd\n";
        return 25;
    }

    rga_buffer_t src = wrapbuffer_fd(src_fd, width, height, src_rga_format,
                                    static_cast<int>(h_stride), static_cast<int>(v_stride));
    rga_buffer_t dst_rgb = wrapbuffer_fd(dst_fd, target_width, target_height,
                                        RK_FORMAT_RGB_888, target_width, target_height);
    im_rect src_rect;
    im_rect dst_rect;
    std::memset(&src_rect, 0, sizeof(src_rect));
    std::memset(&dst_rect, 0, sizeof(dst_rect));
    const int check = imcheck(src, dst_rgb, src_rect, dst_rect);
    if (check != IM_STATUS_NOERROR) {
        std::cerr << "RGA imcheck failed: " << imStrError(static_cast<IM_STATUS>(check)) << "\n";
        return 26;
    }

    const auto start = std::chrono::steady_clock::now();
    const IM_STATUS status = imcvtcolor(src, dst_rgb, src_rga_format, RK_FORMAT_RGB_888,
                                       IM_YUV_TO_RGB_BT601_LIMIT);
    const double ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    if (status != IM_STATUS_SUCCESS) {
        std::cerr << "RGA imcvtcolor failed: " << imStrError(status) << "\n";
        return 27;
    }

    std::cout << "RGA_DMABUF_OK src_fd=" << src_fd << " dst_fd=" << dst_fd
              << " target=" << target_width << "x" << target_height
              << " convert_ms=" << ms << "\n";
    return 0;
}

} // namespace

int main(int argc, char **argv)
{
    if (argc < 3 || argc > 5) {
        std::cerr << "Usage: " << argv[0]
                  << " <annexb.h264|annexb.h265> <h264|h265> [target_width=640] [target_height=640]\n";
        return 2;
    }

    const char *input = argv[1];
    const MppCodingType coding = parse_coding(argv[2]);
    const int target_width = argc > 3 ? std::atoi(argv[3]) : 640;
    const int target_height = argc > 4 ? std::atoi(argv[4]) : 640;
    if (coding == MPP_VIDEO_CodingUnused || target_width <= 0 || target_height <= 0) {
        std::cerr << "invalid codec or target size\n";
        return 2;
    }

    FILE *fp = std::fopen(input, "rb");
    if (!fp) {
        std::perror(input);
        return 3;
    }
    MppContextGuard decoder;
    MPP_RET ret = mpp_create(&decoder.context, &decoder.mpi);
    if (ret != MPP_OK) {
        std::cerr << "mpp_create failed ret=" << ret << "\n";
        std::fclose(fp);
        return 4;
    }
    ret = mpp_init(decoder.context, MPP_CTX_DEC, coding);
    if (ret != MPP_OK) {
        std::cerr << "mpp_init failed ret=" << ret << "\n";
        std::fclose(fp);
        return 5;
    }
    std::string packet_storage(kPacketBufferSize, '\0');
    int packets = 0;
    while (!std::feof(fp)) {
        const size_t read_size = std::fread(&packet_storage[0], 1, packet_storage.size(), fp);
        if (read_size == 0) {
            break;
        }
        ++packets;

        MppPacketGuard packet;
        ret = mpp_packet_init(&packet.packet, &packet_storage[0], read_size);
        if (ret != MPP_OK) {
            std::cerr << "mpp_packet_init failed ret=" << ret << "\n";
            std::fclose(fp);
            return 6;
        }
        if (std::feof(fp)) {
            mpp_packet_set_eos(packet.packet);
        }
        ret = decoder.mpi->decode_put_packet(decoder.context, packet.packet);
        if (ret != MPP_OK) {
            std::cerr << "decode_put_packet failed ret=" << ret << "\n";
            std::fclose(fp);
            return 7;
        }

        for (int tries = 0; tries < 120; ++tries) {
            MppFrameGuard frame;
            ret = decoder.mpi->decode_get_frame(decoder.context, &frame.frame);
            if (ret == MPP_ERR_TIMEOUT) {
                usleep(1000);
                continue;
            }
            if (ret != MPP_OK) {
                std::cerr << "decode_get_frame failed ret=" << ret << "\n";
                std::fclose(fp);
                return 8;
            }
            if (!frame.frame) {
                usleep(1000);
                continue;
            }
            if (mpp_frame_get_info_change(frame.frame)) {
                const RK_U32 width = mpp_frame_get_width(frame.frame);
                const RK_U32 height = mpp_frame_get_height(frame.frame);
                const RK_U32 h_stride = mpp_frame_get_hor_stride(frame.frame);
                const RK_U32 v_stride = mpp_frame_get_ver_stride(frame.frame);
                const RK_U32 buf_size = mpp_frame_get_buf_size(frame.frame);
                std::cout << "INFO_CHANGE width=" << width << " height=" << height
                          << " h_stride=" << h_stride << " v_stride=" << v_stride
                          << " buf_size=" << buf_size << "\n";
                if (!decoder.frame_group) {
                    ret = mpp_buffer_group_get_internal(&decoder.frame_group, MPP_BUFFER_TYPE_DRM);
                    if (ret != MPP_OK) {
                        std::cerr << "failed to create DRM frame group ret=" << ret << "\n";
                        std::fclose(fp);
                        return 9;
                    }
                    ret = decoder.mpi->control(decoder.context, MPP_DEC_SET_EXT_BUF_GROUP,
                                               decoder.frame_group);
                    if (ret != MPP_OK) {
                        std::cerr << "MPP_DEC_SET_EXT_BUF_GROUP failed ret=" << ret << "\n";
                        std::fclose(fp);
                        return 10;
                    }
                }
                ret = decoder.mpi->control(decoder.context, MPP_DEC_SET_INFO_CHANGE_READY, nullptr);
                if (ret != MPP_OK) {
                    std::cerr << "MPP_DEC_SET_INFO_CHANGE_READY failed ret=" << ret << "\n";
                    std::fclose(fp);
                    return 11;
                }
                break;
            }
            if (mpp_frame_get_errinfo(frame.frame) || mpp_frame_get_discard(frame.frame)) {
                std::cerr << "decoded frame has errinfo=" << mpp_frame_get_errinfo(frame.frame)
                          << " discard=" << mpp_frame_get_discard(frame.frame) << "\n";
                std::fclose(fp);
                return 12;
            }
            std::cout << "DECODED packets_used=" << packets << "\n";
            const int rga_ret = run_rga_probe(frame.frame, target_width, target_height);
            std::fclose(fp);
            frame.frame = nullptr;
            packet.packet = nullptr;
            decoder.context = nullptr;
            decoder.frame_group = nullptr;
            return rga_ret;
        }
    }

    std::fclose(fp);
    std::cerr << "no decoded frame produced after packets=" << packets << "\n";
    return 13;
}
