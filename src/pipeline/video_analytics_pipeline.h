#ifndef RK3588_DEMO_VIDEO_ANALYTICS_PIPELINE_H
#define RK3588_DEMO_VIDEO_ANALYTICS_PIPELINE_H

#include <atomic>
#include <string>
#include <thread>
#include "media/media_buffer.h"
#include "yolov5/yolov5_thread_pool.h"

struct StreamPipelineConfig {
    std::string model_path;
    std::string source_url;
    std::string push_url;
    int inference_threads{3};
    std::size_t frame_queue_capacity{10};
};

class VideoAnalyticsPipeline {
public:
    explicit VideoAnalyticsPipeline(StreamPipelineConfig config);
    ~VideoAnalyticsPipeline();
    nn_error_e run();
    void stop();
private:
    void collectResults();
    StreamPipelineConfig config_;
    MediaBuffer media_buffer_;
    Yolov5ThreadPool inference_pool_;
    std::atomic<bool> running_{false};
    std::thread source_thread_;
    std::thread encoder_thread_;
    std::thread result_thread_;
};

#endif
