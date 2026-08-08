#include "video_analytics_pipeline.h"

#include <chrono>
#include <utility>
#include <opencv2/opencv.hpp>
#include "draw/cv_draw.h"
#include "media/zlmedia_worker.h"
#include "utils/logging.h"

extern void *mpi_enc_test(int width, int height);
extern void get_source_shape(int *width, int *height);
extern int init_ffmpeg_source(const char *filepath);

VideoAnalyticsPipeline::VideoAnalyticsPipeline(StreamPipelineConfig config)
    : config_(std::move(config)), media_buffer_(config_.frame_queue_capacity) {}
VideoAnalyticsPipeline::~VideoAnalyticsPipeline() { stop(); }

nn_error_e VideoAnalyticsPipeline::run() {
    nn_error_e ret = inference_pool_.setUp(config_.model_path, config_.inference_threads);
    if (ret != NN_SUCCESS) return ret;
    bind_media_buffer(media_buffer_);
    running_ = true;
    source_thread_ = std::thread(init_ffmpeg_source, config_.source_url.c_str());

    int width = 0, height = 0;
    get_source_shape(&width, &height);
    if (width <= 0 || height <= 0) return NN_IO_NUM_NOT_MATCH;

    init_zlmediakit(width, height, config_.push_url);
    encoder_thread_ = std::thread(mpi_enc_test, width, height);
    result_thread_ = std::thread(&VideoAnalyticsPipeline::collectResults, this);

    int frame_id = 0;
    cv::Mat frame;
    while (running_ && media_buffer_.sourceFrames().pop(frame)) {
        ret = inference_pool_.submitTask(frame, frame_id++);
        if (ret != NN_SUCCESS) break;
    }
    stop();
    return ret;
}

void VideoAnalyticsPipeline::collectResults() {
    int frame_id = 0, frame_count = 0, fps = 0;
    auto started = std::chrono::high_resolution_clock::now();
    while (running_) {
        InferenceResult result;
        nn_error_e ret = inference_pool_.getResult(result, frame_id);
        if (ret == NN_TIMEOUT) continue;
        if (ret != NN_SUCCESS) break;
        ++frame_id;

        DrawDetections(result.frame, result.detections);
        ++frame_count;
        auto now = std::chrono::high_resolution_clock::now();
        float elapsed_ms = std::chrono::duration_cast<std::chrono::microseconds>(now - started).count() / 1000.f;
        if (elapsed_ms > 1000.f) {
            fps = static_cast<int>(frame_count / (elapsed_ms / 1000.f));
            NN_LOG_INFO("Pipeline FPS:%d, Frame Count:%d", fps, frame_count);
            frame_count = 0;
            started = now;
        }
        cv::putText(result.frame, std::to_string(fps) + " fps", cv::Point(50, 100),
                    cv::FONT_HERSHEY_PLAIN, 1.2, cv::Scalar(255, 255, 255), 2);
        media_buffer_.outputFrames().push(result.frame);
    }
}

void VideoAnalyticsPipeline::stop() {
    if (!running_.exchange(false)) return;
    media_buffer_.close();
    inference_pool_.stopAll();
    unbind_media_buffer(media_buffer_);
    if (result_thread_.joinable()) result_thread_.join();
    if (source_thread_.joinable()) source_thread_.join();
    if (encoder_thread_.joinable()) encoder_thread_.join();
}
