// 预处理

#include "preprocess.h"

#include "utils/logging.h"
#include "im2d.h"
#include "rga.h"

#include <algorithm>
#include <opencv2/imgproc.hpp>


// opencv 版本的 letterbox
LetterBoxInfo letterbox(const cv::Mat &img, cv::Mat &img_letterbox, float wh_ratio)
{
    // img has to be 3 channels
    if (img.channels() != 3)
    {
        NN_LOG_ERROR("img has to be 3 channels");
        exit(-1);
    }
    float img_width = img.cols;
    float img_height = img.rows;

    int letterbox_width = 0;
    int letterbox_height = 0;

    LetterBoxInfo info;
    int padding_hor = 0;
    int padding_ver = 0;

    if (img_width / img_height > wh_ratio)
    {
        info.hor = false;
        letterbox_width = img_width;
        letterbox_height = img_width / wh_ratio;
        info.pad = (letterbox_height - img_height) / 2.f;
        padding_hor = 0;
        padding_ver = info.pad;
        
    }
    else
    {
        info.hor = true;
        letterbox_width = img_height * wh_ratio;
        letterbox_height = img_height;
        info.pad = (letterbox_width - img_width) / 2.f;
        padding_hor = info.pad;
        padding_ver = 0;
    }
    // 使用cv::copyMakeBorder函数进行填充边界
    cv::copyMakeBorder(img, img_letterbox, padding_ver, padding_ver, padding_hor, padding_hor, cv::BORDER_CONSTANT, cv::Scalar(0, 0, 0));
    return info;
}

// 直接缩放到模型尺寸内再补边，避免先创建与原图同量级的方形中间图。
LetterBoxInfo letterbox_resize(const cv::Mat &img, cv::Mat &img_letterbox,
                               uint32_t target_width, uint32_t target_height)
{
    LetterBoxInfo info;
    info.original_width = img.cols;
    info.original_height = img.rows;
    const float scale_w = static_cast<float>(target_width) / img.cols;
    const float scale_h = static_cast<float>(target_height) / img.rows;
    info.scale = std::min(scale_w, scale_h);

    const int resized_width = std::max(1, static_cast<int>(std::round(img.cols * info.scale)));
    const int resized_height = std::max(1, static_cast<int>(std::round(img.rows * info.scale)));
    info.pad_x = (static_cast<int>(target_width) - resized_width) / 2;
    info.pad_y = (static_cast<int>(target_height) - resized_height) / 2;
    info.hor = info.pad_x > 0;
    info.pad = info.hor ? info.pad_x : info.pad_y;

    cv::Mat resized;
    cv::resize(img, resized, cv::Size(resized_width, resized_height), 0, 0, cv::INTER_LINEAR);
    const int right = static_cast<int>(target_width) - resized_width - info.pad_x;
    const int bottom = static_cast<int>(target_height) - resized_height - info.pad_y;
    cv::copyMakeBorder(resized, img_letterbox, info.pad_y, bottom, info.pad_x, right,
                       cv::BORDER_CONSTANT, cv::Scalar(0, 0, 0));
    return info;
}

// opencv resize
void cvimg2tensor(const cv::Mat &img, uint32_t width, uint32_t height, tensor_data_s &tensor)
{
    // img has to be 3 channels
    if (img.channels() != 3)
    {
        NN_LOG_ERROR("img has to be 3 channels");
        exit(-1);
    }
    cv::Mat tensor_view(height, width, CV_8UC3, tensor.data);
    if (img.cols == static_cast<int>(width) && img.rows == static_cast<int>(height))
    {
        cv::cvtColor(img, tensor_view, cv::COLOR_BGR2RGB);
        return;
    }
    cv::Mat img_resized;
    cv::resize(img, img_resized, cv::Size(width, height), 0, 0, cv::INTER_LINEAR);
    cv::cvtColor(img_resized, tensor_view, cv::COLOR_BGR2RGB);
}

// rga 版本的 resize
void cvimg2tensor_rga(const cv::Mat &img, uint32_t width, uint32_t height, tensor_data_s &tensor)
{
    // img has to be 3 channels
    if (img.channels() != 3)
    {
        NN_LOG_ERROR("img has to be 3 channels");
        exit(-1);
    }

    cv::Mat img_rgb;
    cv::cvtColor(img, img_rgb, cv::COLOR_BGR2RGB);

    im_rect src_rect;
    im_rect dst_rect;
    memset(&src_rect, 0, sizeof(src_rect));
    memset(&dst_rect, 0, sizeof(dst_rect));
    rga_buffer_t src = wrapbuffer_virtualaddr((void *)img_rgb.data, img.cols, img.rows, RK_FORMAT_RGB_888);
    rga_buffer_t dst = wrapbuffer_virtualaddr((void *)tensor.data, width, height, RK_FORMAT_RGB_888);
    int ret = imcheck(src, dst, src_rect, dst_rect);
    if (IM_STATUS_NOERROR != ret)
    {
        NN_LOG_ERROR("%d, check error! %s", __LINE__, imStrError((IM_STATUS)ret));
        exit(-1);
    }
    imresize(src, dst);
}

// rga 版本的 letterbox
LetterBoxInfo letterbox_rga(const cv::Mat &img, cv::Mat &img_letterbox, float wh_ratio)
{
    // img has to be 3 channels
    if (img.channels() != 3)
    {
        NN_LOG_ERROR("img has to be 3 channels");
        exit(-1);
    }
    float img_width = img.cols;
    float img_height = img.rows;

    int letterbox_width = 0;
    int letterbox_height = 0;

    LetterBoxInfo info;
    int padding_hor = 0;
    int padding_ver = 0;

    if (img_width / img_height > wh_ratio)
    {
        info.hor = false;
        letterbox_width = img_width;
        letterbox_height = img_width / wh_ratio;
        info.pad = (letterbox_height - img_height) / 2.f;
        padding_hor = 0;
        padding_ver = info.pad;
    }
    else
    {
        info.hor = true;
        letterbox_width = img_height * wh_ratio;
        letterbox_height = img_height;
        info.pad = (letterbox_width - img_width) / 2.f;
        padding_hor = info.pad;
        padding_ver = 0;
    }
    // rga add border
    img_letterbox = cv::Mat::zeros(letterbox_height, letterbox_width, CV_8UC3);

    im_rect src_rect;
    im_rect dst_rect;
    memset(&src_rect, 0, sizeof(src_rect));
    memset(&dst_rect, 0, sizeof(dst_rect));

    // NN_LOG_INFO("img size: %d, %d", img.cols, img.rows);

    rga_buffer_t src = wrapbuffer_virtualaddr((void *)img.data, img.cols, img.rows, RK_FORMAT_RGB_888);
    rga_buffer_t dst = wrapbuffer_virtualaddr((void *)img_letterbox.data, img_letterbox.cols, img_letterbox.rows, RK_FORMAT_RGB_888);
    int ret = imcheck(src, dst, src_rect, dst_rect);
    if (IM_STATUS_NOERROR != ret)
    {
        NN_LOG_ERROR("%d, check error! %s", __LINE__, imStrError((IM_STATUS)ret));
        exit(-1);
    }

    immakeBorder(src, dst, padding_ver, padding_ver, padding_hor, padding_hor, 0, 0, 0);

    return info;
}
