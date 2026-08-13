#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <numeric>
#include <string>
#include <thread>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/videoio.hpp>

#include "draw/cv_draw.h"
#include "utils/logging.h"
#include "yolov5/yolov5_thread_pool.h"

struct FrameMetric
{
    int frame_id{0};
    int detections{0};
    int status{0};
    double decode_ms{0.0};
    double queue_ms{0.0};
    double preprocess_ms{0.0};
    double npu_ms{0.0};
    double postprocess_ms{0.0};
    double draw_ms{0.0};
    double e2e_ms{0.0};
};

struct HardwareSample
{
    long long elapsed_ms{0};
    int soc_temp_millic{0};
    int npu_temp_millic{0};
    int rss_kib{0};
    int npu_load[3]{0, 0, 0};
    bool has_npu_load{false};
};

static std::atomic<int> g_submitted{0};
static std::atomic<bool> g_input_done{false};
static std::atomic<bool> g_stop_sampler{false};
static Yolov5ThreadPool *g_pool = nullptr;
static std::vector<HardwareSample> g_hardware_samples;

static int read_integer(const char *path)
{
    std::ifstream file(path);
    int value = 0;
    file >> value;
    return value;
}

static int read_rss_kib()
{
    std::ifstream file("/proc/self/status");
    std::string key;
    while (file >> key)
    {
        if (key == "VmRSS:")
        {
            int value = 0;
            file >> value;
            return value;
        }
        std::string rest;
        std::getline(file, rest);
    }
    return 0;
}

static void sample_hardware()
{
    const auto sampler_start = std::chrono::steady_clock::now();
    while (!g_stop_sampler.load())
    {
        HardwareSample sample;
        sample.elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - sampler_start).count();
        sample.soc_temp_millic = read_integer("/sys/class/thermal/thermal_zone0/temp");
        sample.npu_temp_millic = read_integer("/sys/class/thermal/thermal_zone6/temp");
        sample.rss_kib = read_rss_kib();
        std::ifstream load_file("/sys/kernel/debug/rknpu/load");
        std::string load_line;
        std::getline(load_file, load_line);
        sample.has_npu_load = std::sscanf(load_line.c_str(),
            "NPU load: Core0: %d%%, Core1: %d%%, Core2: %d%%,",
            &sample.npu_load[0], &sample.npu_load[1], &sample.npu_load[2]) == 3;
        g_hardware_samples.push_back(sample);
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
}

static void read_stream(const char *video_file)
{
    cv::VideoCapture cap(video_file);
    if (!cap.isOpened())
    {
        NN_LOG_ERROR("Failed to open video file: %s", video_file);
        g_input_done = true;
        return;
    }
    NN_LOG_INFO("Video size: %d x %d, fps: %.3f",
                static_cast<int>(cap.get(cv::CAP_PROP_FRAME_WIDTH)),
                static_cast<int>(cap.get(cv::CAP_PROP_FRAME_HEIGHT)),
                cap.get(cv::CAP_PROP_FPS));

    cv::Mat frame;
    while (true)
    {
        const auto decode_start = std::chrono::steady_clock::now();
        if (!cap.read(frame) || frame.empty())
            break;
        cv::Mat owned_frame = frame.clone();
        const double decode_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - decode_start).count();
        const int id = g_submitted.load();
        if (g_pool->submitTask(owned_frame, id, decode_ms) != NN_SUCCESS)
            break;
        g_submitted.fetch_add(1);
    }
    g_input_done = true;
    NN_LOG_INFO("Video input end, submitted=%d", g_submitted.load());
}

static double percentile(std::vector<double> values, double p)
{
    if (values.empty()) return 0.0;
    std::sort(values.begin(), values.end());
    const double index = p * static_cast<double>(values.size() - 1);
    const size_t lower = static_cast<size_t>(index);
    const size_t upper = std::min(lower + 1, values.size() - 1);
    const double fraction = index - lower;
    return values[lower] * (1.0 - fraction) + values[upper] * fraction;
}

static void print_distribution(const char *name, const std::vector<double> &values)
{
    if (values.empty()) return;
    const double average = std::accumulate(values.begin(), values.end(), 0.0) / values.size();
    const double maximum = *std::max_element(values.begin(), values.end());
    NN_LOG_INFO("PROFILE %-12s avg=%7.3f p50=%7.3f p95=%7.3f p99=%7.3f max=%7.3f ms",
                name, average, percentile(values, 0.50), percentile(values, 0.95),
                percentile(values, 0.99), maximum);
}

static void write_csv(const std::string &path, const std::vector<FrameMetric> &metrics)
{
    if (path.empty()) return;
    std::ofstream file(path);
    file << "frame_id,status,detections,decode_ms,queue_ms,preprocess_ms,npu_ms,postprocess_ms,draw_ms,e2e_ms\n";
    for (const auto &m : metrics)
    {
        file << m.frame_id << ',' << m.status << ',' << m.detections << ','
             << m.decode_ms << ',' << m.queue_ms << ',' << m.preprocess_ms << ','
             << m.npu_ms << ',' << m.postprocess_ms << ',' << m.draw_ms << ','
             << m.e2e_ms << '\n';
    }
    NN_LOG_INFO("PROFILE_CSV path=%s rows=%ld", path.c_str(), metrics.size());
}

static void write_hardware_csv(const std::string &path)
{
    if (path.empty()) return;
    const std::string hardware_path = path + ".hw.csv";
    std::ofstream file(hardware_path);
    file << "elapsed_ms,rss_kib,soc_temp_c,npu_temp_c,npu_core0_load,npu_core1_load,npu_core2_load\n";
    for (const auto &sample : g_hardware_samples)
    {
        file << sample.elapsed_ms << ',' << sample.rss_kib << ','
             << sample.soc_temp_millic / 1000.0 << ',' << sample.npu_temp_millic / 1000.0 << ',';
        if (sample.has_npu_load)
            file << sample.npu_load[0] << ',' << sample.npu_load[1] << ',' << sample.npu_load[2];
        else
            file << ",,";
        file << '\n';
    }
    NN_LOG_INFO("PROFILE_HW_CSV path=%s rows=%ld", hardware_path.c_str(), g_hardware_samples.size());
}

int main(int argc, char **argv)
{
    if (argc < 3 || argc > 7)
    {
        NN_LOG_ERROR("Usage: %s <model.rknn> <video> [contexts=2] [draw=0|1] [decoder=auto|software] [profile.csv]", argv[0]);
        return 2;
    }
    std::string model_file = argv[1];
    const char *video_file = argv[2];
    const int contexts = argc > 3 ? std::atoi(argv[3]) : 2;
    const bool draw = argc > 4 ? std::atoi(argv[4]) != 0 : false;
    const std::string decoder = argc > 5 ? argv[5] : "auto";
    const std::string profile_path = argc > 6 ? argv[6] : "";
    if (contexts < 1 || contexts > 20)
    {
        NN_LOG_ERROR("contexts must be between 1 and 20");
        return 3;
    }
    if (decoder == "software")
        setenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", "video_codec;h264", 1);
    else if (decoder != "auto")
    {
        NN_LOG_ERROR("decoder must be auto or software");
        return 3;
    }
    NN_LOG_INFO("Decoder mode: %s", decoder.c_str());

    Yolov5ThreadPool pool;
    g_pool = &pool;
    nn_error_e ret = pool.setUp(model_file, contexts);
    if (ret != NN_SUCCESS)
    {
        NN_LOG_ERROR("Thread pool setup failed: error=%d", ret);
        return 4;
    }

    g_hardware_samples.reserve(4096);
    std::thread sampler(sample_hardware);
    const auto start = std::chrono::steady_clock::now();
    std::thread reader(read_stream, video_file);
    std::vector<FrameMetric> metrics;
    metrics.reserve(4096);
    int completed = 0;
    int errors = 0;
    while (!g_input_done.load() || completed < g_submitted.load())
    {
        if (completed >= g_submitted.load())
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        InferenceResult result;
        ret = pool.getResult(result, completed);
        if (ret != NN_SUCCESS)
        {
            NN_LOG_ERROR("Result timeout at frame %d", completed);
            ++errors;
            break;
        }
        const auto draw_start = std::chrono::steady_clock::now();
        if (result.status != NN_SUCCESS)
        {
            NN_LOG_ERROR("Inference failed at frame %d: error=%d", completed, result.status);
            ++errors;
        }
        else if (draw)
            DrawDetections(result.frame, result.detections);
        const auto frame_end = std::chrono::steady_clock::now();

        FrameMetric metric;
        metric.frame_id = result.id;
        metric.status = result.status;
        metric.detections = static_cast<int>(result.detections.size());
        metric.decode_ms = result.decode_ms;
        metric.queue_ms = result.queue_wait_ms;
        metric.preprocess_ms = result.profile.preprocess_ms;
        metric.npu_ms = result.profile.npu_ms;
        metric.postprocess_ms = result.profile.postprocess_ms;
        metric.draw_ms = std::chrono::duration<double, std::milli>(frame_end - draw_start).count();
        metric.e2e_ms = metric.decode_ms + std::chrono::duration<double, std::milli>(
            frame_end - result.enqueued_at).count();
        metrics.push_back(metric);
        ++completed;
    }
    reader.join();
    pool.stopAll();
    g_stop_sampler = true;
    sampler.join();

    const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    const double fps = seconds > 0.0 ? completed / seconds : 0.0;
    NN_LOG_INFO("POOL_OK contexts=%d submitted=%d completed=%d errors=%d draw=%d decoder=%s elapsed_s=%.3f fps=%.3f",
                contexts, g_submitted.load(), completed, errors, draw ? 1 : 0,
                decoder.c_str(), seconds, fps);

    std::vector<double> decode, queue, preprocess, npu, postprocess, drawing, e2e;
    for (const auto &m : metrics)
    {
        decode.push_back(m.decode_ms); queue.push_back(m.queue_ms);
        preprocess.push_back(m.preprocess_ms); npu.push_back(m.npu_ms);
        postprocess.push_back(m.postprocess_ms); drawing.push_back(m.draw_ms);
        e2e.push_back(m.e2e_ms);
    }
    print_distribution("decode", decode);
    print_distribution("queue", queue);
    print_distribution("preprocess", preprocess);
    print_distribution("npu", npu);
    print_distribution("postprocess", postprocess);
    print_distribution("draw", drawing);
    print_distribution("e2e", e2e);

    int max_soc_temp = 0, max_npu_temp = 0, peak_rss = 0;
    int min_rss = g_hardware_samples.empty() ? 0 : g_hardware_samples.front().rss_kib;
    long long load_sum[3]{0, 0, 0};
    int load_samples = 0;
    int max_load[3]{0, 0, 0};
    for (const auto &sample : g_hardware_samples)
    {
        max_soc_temp = std::max(max_soc_temp, sample.soc_temp_millic);
        max_npu_temp = std::max(max_npu_temp, sample.npu_temp_millic);
        peak_rss = std::max(peak_rss, sample.rss_kib);
        min_rss = std::min(min_rss, sample.rss_kib);
        if (sample.has_npu_load)
        {
            ++load_samples;
            for (int i = 0; i < 3; ++i)
            {
                load_sum[i] += sample.npu_load[i];
                max_load[i] = std::max(max_load[i], sample.npu_load[i]);
            }
        }
    }
    NN_LOG_INFO("PROFILE_HW samples=%ld peak_rss_mib=%.1f max_soc_c=%.1f max_npu_c=%.1f",
                g_hardware_samples.size(), peak_rss / 1024.0,
                max_soc_temp / 1000.0, max_npu_temp / 1000.0);
    if (!g_hardware_samples.empty())
        NN_LOG_INFO("PROFILE_MEMORY first_rss_mib=%.1f last_rss_mib=%.1f min_rss_mib=%.1f peak_rss_mib=%.1f",
                    g_hardware_samples.front().rss_kib / 1024.0,
                    g_hardware_samples.back().rss_kib / 1024.0,
                    min_rss / 1024.0, peak_rss / 1024.0);
    if (load_samples > 0)
    {
        NN_LOG_INFO("PROFILE_NPU_LOAD avg=%lld/%lld/%lld max=%d/%d/%d samples=%d",
                    load_sum[0] / load_samples, load_sum[1] / load_samples,
                    load_sum[2] / load_samples, max_load[0], max_load[1],
                    max_load[2], load_samples);
    }
    else
        NN_LOG_INFO("PROFILE_NPU_LOAD unavailable (debugfs permission or kernel support)");

    write_csv(profile_path, metrics);
    write_hardware_csv(profile_path);
    return errors == 0 && completed == g_submitted.load() && completed > 0 ? 0 : 5;
}
