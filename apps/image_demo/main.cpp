
#include <chrono>
#include <cstdio>
#include <opencv2/imgcodecs.hpp>

#include "yolov5/yolov5.h"
#include "utils/logging.h"
#include "draw/cv_draw.h"

int main(int argc, char **argv)
{
    if (argc < 3 || argc > 4)
    {
        std::fprintf(stderr, "Usage: %s <model.rknn> <input.jpg> [output.jpg]\n", argv[0]);
        return 2;
    }
    // model file path
    const char *model_file = argv[1];
    // input img path
    const char *img_file = argv[2];
    // 读取图片
    cv::Mat img = cv::imread(img_file);
    if (img.empty())
    {
        NN_LOG_ERROR("failed to read input image: %s", img_file);
        return 3;
    }
    // print img size
    NN_LOG_INFO("img size: %d x %d", img.cols, img.rows);

    // 初始化
    Yolov5 yolo;
    // 加载模型
    nn_error_e ret = yolo.LoadModel(model_file);
    if (ret != NN_SUCCESS)
    {
        NN_LOG_ERROR("failed to load model: %s, error=%d", model_file, ret);
        return 4;
    }

    // 运行模型
    std::vector<Detection> objects;
    const auto infer_start = std::chrono::steady_clock::now();
    ret = yolo.Run(img, objects);
    const auto infer_end = std::chrono::steady_clock::now();
    if (ret != NN_SUCCESS)
    {
        NN_LOG_ERROR("inference failed: error=%d", ret);
        return 5;
    }

    // 显示结果
    DrawDetections(img, objects);

    // 保存结果
    const char *output_file = argc == 4 ? argv[3] : "result.jpg";
    if (!cv::imwrite(output_file, img))
    {
        NN_LOG_ERROR("failed to write output image: %s", output_file);
        return 6;
    }

    const double infer_ms = std::chrono::duration<double, std::milli>(infer_end - infer_start).count();
    NN_LOG_INFO("INFERENCE_OK detections=%ld infer_ms=%.3f output=%s",
                objects.size(), infer_ms, output_file);

    return 0;
}
