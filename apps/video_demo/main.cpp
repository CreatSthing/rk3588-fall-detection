
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/videoio.hpp>
#include <chrono>
#include <cstdlib>
#include <cstdio>
#include <string>
#include <sys/stat.h>

#include "yolov5/yolov5.h"
#include "utils/logging.h"
#include "draw/cv_draw.h"

int main(int argc, char **argv)
{
    if (argc < 3 || argc > 7)
    {
        NN_LOG_ERROR("Usage: %s <model.rknn> <video-or-camera> [output.mp4|-] [max_frames] [draw=0|1] [decoder=auto|software]", argv[0]);
        return 2;
    }
    // model file path
    const char *model_file = argv[1];
    // input video
    const char *video_file = argv[2];
    const std::string output_file = argc > 3 ? argv[3] : "-";
    const bool record = output_file != "-";
    struct stat output_stat {};
    const bool record_images = record && stat(output_file.c_str(), &output_stat) == 0 &&
                               S_ISDIR(output_stat.st_mode);
    const bool record_video = record && !record_images;
    const int max_frames = argc > 4 ? std::atoi(argv[4]) : 0;
    const bool draw = argc > 5 ? std::atoi(argv[5]) != 0 : true;
    const std::string decoder = argc > 6 ? argv[6] : "auto";
    if (decoder == "software")
    {
        setenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", "video_codec;h264", 1);
    }
    else if (decoder != "auto")
    {
        NN_LOG_ERROR("decoder must be auto or software");
        return 2;
    }

    // 读取视频
    cv::VideoCapture cap(video_file);
    if (!cap.isOpened())
    {
        NN_LOG_ERROR("Failed to open video file: %s", video_file);
        return 3;
    }
    // 获取视频尺寸、帧率
    int width = cap.get(cv::CAP_PROP_FRAME_WIDTH);
    int height = cap.get(cv::CAP_PROP_FRAME_HEIGHT);
    double fps = cap.get(cv::CAP_PROP_FPS);
    if (width <= 0 || height <= 0)
    {
        NN_LOG_ERROR("Invalid video dimensions: %d x %d", width, height);
        return 4;
    }
    if (fps <= 0.0 || fps > 240.0)
    {
        NN_LOG_INFO("Invalid source FPS %.3f; using 25 FPS for output", fps);
        fps = 25.0;
    }
    NN_LOG_INFO("Video size: %d x %d, fps: %.3f", width, height, fps);

    // 初始化
    Yolov5 yolo;
    // 加载模型
    nn_error_e ret = yolo.LoadModel(model_file);
    if (ret != NN_SUCCESS)
    {
        NN_LOG_ERROR("Failed to load model: %s, error=%d", model_file, ret);
        return 5;
    }
    // 视频帧
    cv::Mat img;
    cv::VideoWriter writer;
    if (record_video)
    {
        // 写入视频mp4文件
        writer = cv::VideoWriter(output_file, cv::VideoWriter::fourcc('m', 'p', '4', 'v'), fps, cv::Size(width, height));
        if (!writer.isOpened())
        {
            NN_LOG_ERROR("Failed to open video output: %s", output_file.c_str());
            return 6;
        }
    }

    // all start
    auto start_all = std::chrono::high_resolution_clock::now();
    int frame_count = 0;
    int total_frames = 0;
    int inference_errors = 0;
    double total_read_ms = 0.0;
    double total_infer_ms = 0.0;

    while (true)
    {
        // 开始计时
        auto start_1 = std::chrono::high_resolution_clock::now();

        // 读取视频帧
        cap >> img;
        if (img.empty())
        {
            NN_LOG_INFO("Video end.");
            break;
        }

        // 记录读取视频帧的时间：读取视频帧的时间
        auto end_1 = std::chrono::high_resolution_clock::now();
        // microseconds 微秒，milliseconds 毫秒，seconds 秒，1微妙=0.001毫秒 = 0.000001秒
        auto elapsed_1 = std::chrono::duration_cast<std::chrono::microseconds>(end_1 - start_1).count() / 1000.0;

        // 开始计时
        auto start_2 = std::chrono::high_resolution_clock::now();
        // 检测结果
        std::vector<Detection> objects;
        
        // 运行模型
        ret = yolo.Run(img, objects);
        if (ret != NN_SUCCESS)
        {
            ++inference_errors;
            NN_LOG_ERROR("Inference failed at frame %d: error=%d", total_frames, ret);
            if (inference_errors >= 3)
            {
                NN_LOG_ERROR("Stopping after %d inference errors", inference_errors);
                return 7;
            }
            continue;
        }
        // 绘制框，显示结果
        if (draw)
        {
            DrawDetections(img, objects);
        }

        // 结束计时
        auto end_2 = std::chrono::high_resolution_clock::now();
        auto elapsed_2 = std::chrono::duration_cast<std::chrono::microseconds>(end_2 - start_2).count() / 1000.0;

        // 算法1：计算单张图片的总耗时
        // 总时间
        auto duration = std::chrono::duration_cast<std::chrono::microseconds>(end_2 - start_1).count() / 1000.0;
        // 计算帧率
        auto fps = 1000.0f / duration;

        // 如果计算帧率，输出帧率
        // 输出时间：读取视频帧的时间、模型运行时间、总时间
        total_read_ms += elapsed_1;
        total_infer_ms += elapsed_2;

        // 算法2：计算超过 1s 一共处理了多少张图片，即平均帧率
        frame_count++;
        total_frames++;
        auto elapsed_all_2 = std::chrono::duration_cast<std::chrono::microseconds>(end_2 - start_all).count() / 1000.f;
        // 每隔1秒打印一次
        if (elapsed_all_2 > 1000)
        {

            NN_LOG_INFO("Method2 Time:%fms, FPS:%f, Frame Count:%d", elapsed_all_2, frame_count / (elapsed_all_2 / 1000.0f), frame_count);
            frame_count = 0;
            start_all = std::chrono::high_resolution_clock::now();
        }

        // 如果不计算帧率，就绘制总耗时和帧率, 写入视频帧。（因为method2会计入这个时间）
        if (record_video)
        {
            // 绘制总耗时和帧率
            auto time_str = std::to_string(duration) + "ms";
            auto fps_str = std::to_string(fps) + "fps";
            cv::putText(img, time_str, cv::Point(50, 50), cv::FONT_HERSHEY_PLAIN, 1.2, cv::Scalar(0xFF, 0xFF, 0xFF), 2);
            cv::putText(img, fps_str, cv::Point(50, 100), cv::FONT_HERSHEY_PLAIN, 1.2, cv::Scalar(0xFF, 0xFF, 0xFF), 2);

            // 写入视频帧
            writer << img;
        }
        else if (record_images)
        {
            char frame_path[512];
            std::snprintf(frame_path, sizeof(frame_path), "%s/frame_%06d.jpg",
                          output_file.c_str(), total_frames);
            if (!cv::imwrite(frame_path, img))
            {
                NN_LOG_ERROR("Failed to write frame image: %s", frame_path);
                return 9;
            }
        }
        if (max_frames > 0 && total_frames >= max_frames)
        {
            NN_LOG_INFO("Reached max_frames=%d", max_frames);
            break;
        }
    }

    if (total_frames == 0)
    {
        NN_LOG_ERROR("No frames were processed");
        return 8;
    }
    NN_LOG_INFO("VIDEO_OK frames=%d errors=%d avg_read_ms=%.3f avg_infer_ms=%.3f draw=%d output=%s",
                total_frames, inference_errors, total_read_ms / total_frames,
                total_infer_ms / total_frames, draw ? 1 : 0,
                record ? output_file.c_str() : "disabled");

    return 0;
}
