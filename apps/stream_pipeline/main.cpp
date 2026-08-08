#include <cstdlib>
#include <iostream>
#include "pipeline/video_analytics_pipeline.h"

int main(int argc, char **argv) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0]
                  << " <model.rknn> <source_url> [inference_threads] [push_url]\n";
        return 1;
    }
    StreamPipelineConfig config;
    config.model_path = argv[1];
    config.source_url = argv[2];
    config.inference_threads = argc > 3 ? std::atoi(argv[3]) : 3;
    config.push_url = argc > 4 ? argv[4] : "rtmp://192.168.1.243:1935/live/camera1";
    VideoAnalyticsPipeline pipeline(config);
    return pipeline.run();
}
