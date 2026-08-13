// 预处理

#ifndef RK3588_DEMO_PREPROCESS_H
#define RK3588_DEMO_PREPROCESS_H

#include <opencv2/core.hpp>
#include "types/datatype.h"

struct LetterBoxInfo
{
    bool hor;
    int pad;
    float scale{1.0f};
    int pad_x{0};
    int pad_y{0};
    int original_width{0};
    int original_height{0};
};

LetterBoxInfo letterbox(const cv::Mat &img, cv::Mat &img_letterbox, float wh_ratio);
LetterBoxInfo letterbox_resize(const cv::Mat &img, cv::Mat &img_letterbox,
                               uint32_t target_width, uint32_t target_height);
LetterBoxInfo letterbox_rga(const cv::Mat& img, cv::Mat& img_letterbox, float wh_ratio);
void cvimg2tensor(const cv::Mat &img, uint32_t width, uint32_t height, tensor_data_s &tensor);
void cvimg2tensor_rga(const cv::Mat &img, uint32_t width, uint32_t height, tensor_data_s &tensor);

#endif // RK3588_DEMO_PREPROCESS_H
