#include "yolov5_thread_pool.h"

#include "utils/logging.h"

Yolov5ThreadPool::Yolov5ThreadPool() = default;
Yolov5ThreadPool::~Yolov5ThreadPool() {
    stopAll();
    for (auto &thread : threads_)
        if (thread.joinable()) thread.join();
}

nn_error_e Yolov5ThreadPool::setUp(std::string &model_path, int num_threads) {
    if (num_threads <= 0) return NN_IO_NUM_NOT_MATCH;
    for (int i = 0; i < num_threads; ++i) {
        auto detector = std::make_shared<Yolov5>();
        nn_error_e ret = detector->LoadModel(model_path.c_str());
        if (ret != NN_SUCCESS) return ret;
        instances_.push_back(detector);
    }
    for (int i = 0; i < num_threads; ++i)
        threads_.emplace_back(&Yolov5ThreadPool::worker, this, i);
    return NN_SUCCESS;
}

void Yolov5ThreadPool::worker(int worker_id) {
    while (true) {
        std::pair<int, cv::Mat> task;
        {
            std::unique_lock<std::mutex> lock(task_mutex_);
            task_ready_.wait(lock, [this] { return stopped_ || !tasks_.empty(); });
            if (stopped_ && tasks_.empty()) return;
            task = std::move(tasks_.front());
            tasks_.pop();
            task_space_.notify_one();
        }

        InferenceResult result;
        result.id = task.first;
        result.frame = std::move(task.second);
        instances_[worker_id]->Run(result.frame, result.detections);

        {
            std::lock_guard<std::mutex> lock(result_mutex_);
            results_.emplace(result.id, std::move(result));
        }
        result_ready_.notify_all();
    }
}

nn_error_e Yolov5ThreadPool::submitTask(const cv::Mat &img, int id) {
    std::unique_lock<std::mutex> lock(task_mutex_);
    task_space_.wait(lock, [this] { return stopped_ || tasks_.size() < kMaxPendingTasks; });
    if (stopped_) return NN_TIMEOUT;
    tasks_.push({id, img});
    lock.unlock();
    task_ready_.notify_one();
    return NN_SUCCESS;
}

nn_error_e Yolov5ThreadPool::getResult(InferenceResult &result, int id) {
    std::unique_lock<std::mutex> lock(result_mutex_);
    bool available = result_ready_.wait_for(lock, std::chrono::seconds(5), [this, id] {
        return stopped_ || results_.find(id) != results_.end();
    });
    auto it = results_.find(id);
    if (!available || it == results_.end()) return NN_TIMEOUT;
    result = std::move(it->second);
    results_.erase(it);
    return NN_SUCCESS;
}

nn_error_e Yolov5ThreadPool::getTargetResult(std::vector<Detection> &objects, int id) {
    InferenceResult result;
    nn_error_e ret = getResult(result, id);
    if (ret == NN_SUCCESS) objects = std::move(result.detections);
    return ret;
}

nn_error_e Yolov5ThreadPool::getTargetImgResult(cv::Mat &img, int id) {
    InferenceResult result;
    nn_error_e ret = getResult(result, id);
    if (ret == NN_SUCCESS) img = std::move(result.frame);
    return ret;
}

void Yolov5ThreadPool::stopAll() {
    {
        std::lock_guard<std::mutex> lock(task_mutex_);
        stopped_ = true;
    }
    task_ready_.notify_all();
    task_space_.notify_all();
    result_ready_.notify_all();
}
