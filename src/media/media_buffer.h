#ifndef RK3588_DEMO_MEDIA_BUFFER_H
#define RK3588_DEMO_MEDIA_BUFFER_H

#include <condition_variable>
#include <cstddef>
#include <deque>
#include <mutex>
#include <opencv2/opencv.hpp>

class FrameQueue {
public:
    explicit FrameQueue(std::size_t capacity = 10);
    bool push(const cv::Mat &frame);
    bool pop(cv::Mat &frame);
    void close();
private:
    std::size_t capacity_;
    bool closed_{false};
    std::deque<cv::Mat> frames_;
    std::mutex mutex_;
    std::condition_variable ready_;
};

class MediaBuffer {
public:
    explicit MediaBuffer(std::size_t capacity = 10);
    FrameQueue &sourceFrames();
    FrameQueue &outputFrames();
    void close();
private:
    FrameQueue source_frames_;
    FrameQueue output_frames_;
};

// Temporary adapter for the existing MPP callbacks. Ownership stays with the app.
void bind_media_buffer(MediaBuffer &buffer);
void unbind_media_buffer(MediaBuffer &buffer);

// Legacy API retained for the other demo executables.
void init_media_buffer();
void push_src_media(cv::Mat img);
cv::Mat pop_src_media();
void push_out_media(cv::Mat img);
cv::Mat pop_out_media();

#endif
