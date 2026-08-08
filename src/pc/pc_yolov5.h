#ifndef RK3588_DEMO_PC_YOLOV5_H
#define RK3588_DEMO_PC_YOLOV5_H

#include <string>
#include <vector>

#include <opencv2/dnn.hpp>
#include <opencv2/opencv.hpp>

#include "types/yolo_datatype.h"

class PcYolov5
{
public:
    bool load(const std::string &model_path, const std::string &labels_path = "");
    bool detect(const cv::Mat &frame, std::vector<Detection> &detections);

    void setThresholds(float confidence_threshold, float nms_threshold);

private:
    cv::Mat letterbox(const cv::Mat &frame, float &scale, int &pad_x, int &pad_y) const;
    bool decode(const cv::Mat &output, const cv::Size &original_size,
                float scale, int pad_x, int pad_y,
                std::vector<Detection> &detections) const;
    std::string className(int class_id) const;

    cv::dnn::Net net_;
    std::vector<std::string> labels_;
    int input_width_{640};
    int input_height_{640};
    float confidence_threshold_{0.45f};
    float nms_threshold_{0.45f};
};

#endif
