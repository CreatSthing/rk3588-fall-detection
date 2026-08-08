#ifndef RK3588_DEMO_YOLOV5_THREAD_POOL_H
#define RK3588_DEMO_YOLOV5_THREAD_POOL_H

#include "yolov5.h"
#include <atomic>
#include <condition_variable>
#include <map>
#include <mutex>
#include <queue>
#include <thread>
#include <vector>

struct InferenceResult {
    int id{-1};
    cv::Mat frame;
    std::vector<Detection> detections;
};

class Yolov5ThreadPool {
public:
    Yolov5ThreadPool();
    ~Yolov5ThreadPool();

    nn_error_e setUp(std::string &model_path, int num_threads = 12);
    nn_error_e submitTask(const cv::Mat &img, int id);
    nn_error_e getResult(InferenceResult &result, int id);

    // Compatibility helpers for existing demos.
    nn_error_e getTargetResult(std::vector<Detection> &objects, int id);
    nn_error_e getTargetImgResult(cv::Mat &img, int id);
    void stopAll();

private:
    void worker(int id);

    std::queue<std::pair<int, cv::Mat>> tasks_;
    std::vector<std::shared_ptr<Yolov5>> instances_;
    std::map<int, InferenceResult> results_;
    std::vector<std::thread> threads_;
    std::mutex task_mutex_;
    std::mutex result_mutex_;
    std::condition_variable task_ready_;
    std::condition_variable result_ready_;
    std::condition_variable task_space_;
    std::atomic<bool> stopped_{false};
    static const std::size_t kMaxPendingTasks = 10;
};

#endif
