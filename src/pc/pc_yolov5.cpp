#include "pc_yolov5.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>

bool PcYolov5::load(const std::string &model_path, const std::string &labels_path)
{
    try
    {
        net_ = cv::dnn::readNetFromONNX(model_path);
        net_.setPreferableBackend(cv::dnn::DNN_BACKEND_OPENCV);
        net_.setPreferableTarget(cv::dnn::DNN_TARGET_CPU);
    }
    catch (const cv::Exception &error)
    {
        std::cerr << "Failed to load ONNX model: " << error.what() << '\n';
        return false;
    }

    if (!labels_path.empty())
    {
        std::ifstream labels_file(labels_path);
        std::string label;
        while (std::getline(labels_file, label))
        {
            if (!label.empty()) labels_.push_back(label);
        }
    }
    return !net_.empty();
}

void PcYolov5::setThresholds(float confidence_threshold, float nms_threshold)
{
    confidence_threshold_ = confidence_threshold;
    nms_threshold_ = nms_threshold;
}

cv::Mat PcYolov5::letterbox(const cv::Mat &frame, float &scale, int &pad_x, int &pad_y) const
{
    scale = std::min(input_width_ / static_cast<float>(frame.cols),
                     input_height_ / static_cast<float>(frame.rows));
    int resized_width = static_cast<int>(std::round(frame.cols * scale));
    int resized_height = static_cast<int>(std::round(frame.rows * scale));
    pad_x = (input_width_ - resized_width) / 2;
    pad_y = (input_height_ - resized_height) / 2;

    cv::Mat resized;
    cv::resize(frame, resized, cv::Size(resized_width, resized_height));
    cv::Mat padded(input_height_, input_width_, CV_8UC3, cv::Scalar(114, 114, 114));
    resized.copyTo(padded(cv::Rect(pad_x, pad_y, resized_width, resized_height)));
    return padded;
}

bool PcYolov5::detect(const cv::Mat &frame, std::vector<Detection> &detections)
{
    detections.clear();
    if (frame.empty() || net_.empty()) return false;

    float scale = 1.0f;
    int pad_x = 0;
    int pad_y = 0;
    cv::Mat input = letterbox(frame, scale, pad_x, pad_y);
    cv::Mat blob = cv::dnn::blobFromImage(input, 1.0 / 255.0,
                                          cv::Size(input_width_, input_height_),
                                          cv::Scalar(), true, false);
    net_.setInput(blob);

    std::vector<cv::Mat> outputs;
    try
    {
        net_.forward(outputs, net_.getUnconnectedOutLayersNames());
    }
    catch (const cv::Exception &error)
    {
        std::cerr << "ONNX inference failed: " << error.what() << '\n';
        return false;
    }

    if (outputs.size() != 1)
    {
        std::cerr << "Unsupported YOLO output count: " << outputs.size()
                  << ". Expected one [1, N, 85] output.\n";
        return false;
    }
    return decode(outputs[0], frame.size(), scale, pad_x, pad_y, detections);
}

bool PcYolov5::decode(const cv::Mat &output, const cv::Size &original_size,
                      float scale, int pad_x, int pad_y,
                      std::vector<Detection> &detections) const
{
    if (output.dims != 3 || output.size[0] != 1 || output.size[2] < 6)
    {
        std::cerr << "Unsupported output shape. Expected [1, N, 5 + classes].\n";
        return false;
    }

    const int rows = output.size[1];
    const int dimensions = output.size[2];
    const float *data = reinterpret_cast<const float *>(output.data);
    std::vector<cv::Rect> boxes;
    std::vector<cv::Rect> nms_boxes;
    std::vector<float> scores;
    std::vector<int> class_ids;

    for (int row = 0; row < rows; ++row, data += dimensions)
    {
        float objectness = data[4];
        if (objectness < confidence_threshold_) continue;

        cv::Mat class_scores(1, dimensions - 5, CV_32FC1,
                             const_cast<float *>(data + 5));
        cv::Point class_id_point;
        double best_class_score = 0.0;
        cv::minMaxLoc(class_scores, nullptr, &best_class_score, nullptr, &class_id_point);
        float confidence = objectness * static_cast<float>(best_class_score);
        if (confidence < confidence_threshold_) continue;

        float center_x = (data[0] - pad_x) / scale;
        float center_y = (data[1] - pad_y) / scale;
        float width = data[2] / scale;
        float height = data[3] / scale;
        int left = static_cast<int>(center_x - width / 2.0f);
        int top = static_cast<int>(center_y - height / 2.0f);
        cv::Rect box(left, top, static_cast<int>(width), static_cast<int>(height));
        box &= cv::Rect(0, 0, original_size.width, original_size.height);
        if (box.empty()) continue;

        boxes.push_back(box);
        cv::Rect nms_box = box;
        const int class_offset = class_id_point.x * std::max(original_size.width, original_size.height);
        nms_box.x += class_offset;
        nms_box.y += class_offset;
        nms_boxes.push_back(nms_box);
        scores.push_back(confidence);
        class_ids.push_back(class_id_point.x);
    }

    std::vector<int> kept_indices;
    cv::dnn::NMSBoxes(nms_boxes, scores, confidence_threshold_, nms_threshold_, kept_indices);
    for (int index : kept_indices)
    {
        Detection detection;
        detection.class_id = class_ids[index];
        detection.className = className(detection.class_id);
        detection.confidence = scores[index];
        detection.box = boxes[index];
        detection.color = cv::Scalar(0, 255, 0);
        detections.push_back(detection);
    }
    return true;
}

std::string PcYolov5::className(int class_id) const
{
    if (class_id >= 0 && class_id < static_cast<int>(labels_.size()))
        return labels_[class_id];
    return "class_" + std::to_string(class_id);
}
