#ifndef RK3588_DEMO_YOLOV5_THREAD_POOL_H
#define RK3588_DEMO_YOLOV5_THREAD_POOL_H

#include "yolov5.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <map>
#include <mutex>
#include <queue>
#include <thread>
#include <vector>

struct InferenceResult {
    int id{-1};
    nn_error_e status{NN_SUCCESS};
    cv::Mat frame;
    bool prepared_rgb{false};
    LetterBoxInfo letterbox_info;
    std::vector<Detection> detections;
    InferenceProfile profile;
    double decode_ms{0.0};
    double queue_wait_ms{0.0};
    std::chrono::steady_clock::time_point enqueued_at;
};

struct InferenceTask {
    int id{-1};
    cv::Mat frame;
    bool prepared_rgb{false};
    LetterBoxInfo letterbox_info;
    double decode_ms{0.0};
    std::chrono::steady_clock::time_point enqueued_at;
};

class Yolov5ThreadPool {
public:
    Yolov5ThreadPool();
    ~Yolov5ThreadPool();

    nn_error_e setUp(std::string &model_path, int num_threads = 12);
    nn_error_e submitTask(const cv::Mat &img, int id, double decode_ms = 0.0);
    nn_error_e submitPreparedRgb(const cv::Mat &model_rgb, const LetterBoxInfo &letterbox_info,
                                 int id, double decode_ms = 0.0);
    nn_error_e getResult(InferenceResult &result, int id);

    // Compatibility helpers for existing demos.
    nn_error_e getTargetResult(std::vector<Detection> &objects, int id);
    nn_error_e getTargetImgResult(cv::Mat &img, int id);
    void stopAll();

private:
    void worker(int id);

    std::queue<InferenceTask> tasks_;
    std::vector<std::shared_ptr<Yolov5>> instances_;
    std::map<int, InferenceResult> results_;
    std::vector<std::thread> threads_;
    std::mutex task_mutex_;
    std::mutex result_mutex_;
    std::condition_variable task_ready_;
    std::condition_variable result_ready_;
    std::condition_variable task_space_;
    std::atomic<bool> stopped_{false};
    // 与 RK3588 的 3 个并发 context 对齐，限制离线/突发输入造成的旧帧积压。
    static const std::size_t kMaxPendingTasks = 3;
};

#endif
