

#ifndef RK3588_DEMO_CV_DRAW_H
#define RK3588_DEMO_CV_DRAW_H

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

#include "types/yolo_datatype.h"

// draw detections on img
void DrawDetections(cv::Mat& img, const std::vector<Detection>& objects);

#endif //RK3588_DEMO_CV_DRAW_H
