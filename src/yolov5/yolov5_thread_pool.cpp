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
        InferenceTask task;
        {
            std::unique_lock<std::mutex> lock(task_mutex_);
            task_ready_.wait(lock, [this] { return stopped_ || !tasks_.empty(); });
            if (stopped_ && tasks_.empty()) return;
            task = std::move(tasks_.front());
            tasks_.pop();
            task_space_.notify_one();
        }

        InferenceResult result;
        result.id = task.id;
        result.frame = std::move(task.frame);
        result.prepared_rgb = task.prepared_rgb;
        result.letterbox_info = task.letterbox_info;
        result.decode_ms = task.decode_ms;
        result.enqueued_at = task.enqueued_at;
        result.queue_wait_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - task.enqueued_at).count();
        if (task.prepared_rgb) {
            result.status = instances_[worker_id]->RunPreparedRgb(
                result.frame, task.letterbox_info, result.detections, &result.profile);
        } else {
            result.status = instances_[worker_id]->Run(result.frame, result.detections, &result.profile);
        }

        {
            std::lock_guard<std::mutex> lock(result_mutex_);
            results_.emplace(result.id, std::move(result));
        }
        result_ready_.notify_all();
    }
}

nn_error_e Yolov5ThreadPool::submitTask(const cv::Mat &img, int id, double decode_ms) {
    std::unique_lock<std::mutex> lock(task_mutex_);
    task_space_.wait(lock, [this] { return stopped_ || tasks_.size() < kMaxPendingTasks; });
    if (stopped_) return NN_TIMEOUT;
    InferenceTask task;
    task.id = id;
    task.frame = img;
    task.decode_ms = decode_ms;
    task.enqueued_at = std::chrono::steady_clock::now();
    tasks_.push(std::move(task));
    lock.unlock();
    task_ready_.notify_one();
    return NN_SUCCESS;
}

nn_error_e Yolov5ThreadPool::submitPreparedRgb(const cv::Mat &model_rgb,
                                               const LetterBoxInfo &letterbox_info,
                                               int id, double decode_ms) {
    std::unique_lock<std::mutex> lock(task_mutex_);
    task_space_.wait(lock, [this] { return stopped_ || tasks_.size() < kMaxPendingTasks; });
    if (stopped_) return NN_TIMEOUT;
    InferenceTask task;
    task.id = id;
    task.frame = model_rgb;
    task.prepared_rgb = true;
    task.letterbox_info = letterbox_info;
    task.decode_ms = decode_ms;
    task.enqueued_at = std::chrono::steady_clock::now();
    tasks_.push(std::move(task));
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
