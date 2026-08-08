#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>

#include <opencv2/opencv.hpp>

#include "draw/cv_draw.h"
#include "pc/pc_yolov5.h"

namespace
{
bool isInteger(const std::string &text)
{
    if (text.empty()) return false;
    for (char ch : text)
        if (ch < '0' || ch > '9') return false;
    return true;
}

int runImage(PcYolov5 &detector, const cv::Mat &image, const std::string &output_path)
{
    cv::Mat result = image.clone();
    std::vector<Detection> detections;
    if (!detector.detect(result, detections)) return 1;
    DrawDetections(result, detections);
    if (!cv::imwrite(output_path, result))
    {
        std::cerr << "Failed to write image: " << output_path << '\n';
        return 1;
    }
    std::cout << "Saved image: " << output_path
              << ", detections: " << detections.size() << '\n';
    return 0;
}
}

int main(int argc, char **argv)
{
    if (argc < 3)
    {
        std::cerr << "Usage: " << argv[0]
                  << " <yolov5.onnx> <image|video|camera_index> [output] [labels.txt]\n";
        return 1;
    }

    const std::string model_path = argv[1];
    const std::string input = argv[2];
    const std::string labels_path = argc > 4 ? argv[4] : "";
    PcYolov5 detector;
    if (!detector.load(model_path, labels_path)) return 1;

    if (!isInteger(input))
    {
        cv::Mat image = cv::imread(input);
        if (!image.empty())
        {
            const std::string output_path = argc > 3 ? argv[3] : "pc_result.jpg";
            return runImage(detector, image, output_path);
        }
    }

    cv::VideoCapture capture;
    if (isInteger(input)) capture.open(std::atoi(input.c_str()));
    else capture.open(input);
    if (!capture.isOpened())
    {
        std::cerr << "Failed to open input: " << input << '\n';
        return 1;
    }

    cv::Mat frame;
    if (!capture.read(frame) || frame.empty())
    {
        std::cerr << "Input contains no video frame.\n";
        return 1;
    }

    double fps = capture.get(cv::CAP_PROP_FPS);
    if (fps <= 0.0 || fps > 240.0) fps = 25.0;
    const std::string output_path = argc > 3 ? argv[3] : "pc_result.mp4";
    cv::VideoWriter writer(output_path, cv::VideoWriter::fourcc('m', 'p', '4', 'v'),
                           fps, frame.size());
    if (!writer.isOpened())
    {
        std::cerr << "Failed to create output video: " << output_path << '\n';
        return 1;
    }

    int frame_count = 0;
    auto started = std::chrono::steady_clock::now();
    do
    {
        std::vector<Detection> detections;
        if (!detector.detect(frame, detections)) return 1;
        DrawDetections(frame, detections);
        writer.write(frame);
        ++frame_count;

        if (isInteger(input))
        {
            cv::imshow("PC YOLOv5 Test", frame);
            if (cv::waitKey(1) == 27) break;
        }
    } while (capture.read(frame) && !frame.empty());

    double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    std::cout << "Saved video: " << output_path << '\n'
              << "Frames: " << frame_count << ", average FPS: "
              << (elapsed > 0.0 ? frame_count / elapsed : 0.0) << '\n';
    return 0;
}
