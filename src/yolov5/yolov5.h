#ifndef RK3588_DEMO_YOLOV5_H
#define RK3588_DEMO_YOLOV5_H

#include "types/yolo_datatype.h"
#include "engine/engine.h"
#include "process/preprocess.h"

struct InferenceProfile
{
    double preprocess_ms{0.0};
    double npu_ms{0.0};
    double postprocess_ms{0.0};
};

class Yolov5
{
public:
    Yolov5();
    ~Yolov5();

    nn_error_e LoadModel(const char *model_path);
    nn_error_e Run(const cv::Mat &img, std::vector<Detection> &objects,
                   InferenceProfile *profile = nullptr);
    nn_error_e RunPreparedRgb(const cv::Mat &model_rgb,
                              const LetterBoxInfo &letterbox_info,
                              std::vector<Detection> &objects,
                              InferenceProfile *profile = nullptr);

private:
    nn_error_e Preprocess(const cv::Mat &img, const std::string process_type,
                          cv::Mat &image_letterbox);
    nn_error_e Inference();
    nn_error_e Postprocess(const cv::Mat &img, std::vector<Detection> &objects);

    LetterBoxInfo letterbox_info_;
    tensor_data_s input_tensor_;
    std::vector<tensor_data_s> output_tensors_;
    std::vector<int32_t> out_zps_;
    std::vector<float> out_scales_;
    std::shared_ptr<NNEngine> engine_;
};

#endif // RK3588_DEMO_YOLOV5_H
