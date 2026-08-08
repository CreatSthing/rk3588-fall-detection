#include "media_buffer.h"

#include <memory>

FrameQueue::FrameQueue(std::size_t capacity) : capacity_(capacity) {}

bool FrameQueue::push(const cv::Mat &frame) {
    if (frame.empty()) return false;
    std::lock_guard<std::mutex> lock(mutex_);
    if (closed_) return false;
    frames_.push_back(frame);
    if (frames_.size() > capacity_) frames_.pop_front();
    ready_.notify_one();
    return true;
}

bool FrameQueue::pop(cv::Mat &frame) {
    std::unique_lock<std::mutex> lock(mutex_);
    ready_.wait(lock, [this] { return closed_ || !frames_.empty(); });
    if (frames_.empty()) return false;
    frame = frames_.front();
    frames_.pop_front();
    return true;
}

void FrameQueue::close() {
    std::lock_guard<std::mutex> lock(mutex_);
    closed_ = true;
    ready_.notify_all();
}

MediaBuffer::MediaBuffer(std::size_t capacity)
    : source_frames_(capacity), output_frames_(capacity) {}
FrameQueue &MediaBuffer::sourceFrames() { return source_frames_; }
FrameQueue &MediaBuffer::outputFrames() { return output_frames_; }
void MediaBuffer::close() { source_frames_.close(); output_frames_.close(); }

namespace {
std::mutex g_binding_mutex;
MediaBuffer *g_bound_buffer = nullptr;
std::unique_ptr<MediaBuffer> g_legacy_buffer;
MediaBuffer *boundBuffer() {
    std::lock_guard<std::mutex> lock(g_binding_mutex);
    return g_bound_buffer;
}
}

void bind_media_buffer(MediaBuffer &buffer) {
    std::lock_guard<std::mutex> lock(g_binding_mutex);
    g_bound_buffer = &buffer;
}
void unbind_media_buffer(MediaBuffer &buffer) {
    std::lock_guard<std::mutex> lock(g_binding_mutex);
    if (g_bound_buffer == &buffer) g_bound_buffer = nullptr;
}
void init_media_buffer() {
    std::lock_guard<std::mutex> lock(g_binding_mutex);
    g_legacy_buffer.reset(new MediaBuffer());
    g_bound_buffer = g_legacy_buffer.get();
}
void push_src_media(cv::Mat img) {
    MediaBuffer *buffer = boundBuffer();
    if (buffer) buffer->sourceFrames().push(img);
}
cv::Mat pop_src_media() {
    cv::Mat img;
    MediaBuffer *buffer = boundBuffer();
    if (buffer) buffer->sourceFrames().pop(img);
    return img;
}
void push_out_media(cv::Mat img) {
    MediaBuffer *buffer = boundBuffer();
    if (buffer) buffer->outputFrames().push(img);
}
cv::Mat pop_out_media() {
    cv::Mat img;
    MediaBuffer *buffer = boundBuffer();
    if (buffer) buffer->outputFrames().pop(img);
    return img;
}
