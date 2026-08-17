#include <rknn_api.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <numeric>
#include <string>
#include <vector>

namespace
{
std::vector<unsigned char> read_file(const char *path)
{
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file)
        return {};
    const std::streamsize size = file.tellg();
    if (size <= 0)
        return {};
    std::vector<unsigned char> data(static_cast<size_t>(size));
    file.seekg(0);
    if (!file.read(reinterpret_cast<char *>(data.data()), size))
        return {};
    return data;
}

double percentile(const std::vector<double> &sorted, double fraction)
{
    const size_t index = static_cast<size_t>((sorted.size() - 1) * fraction);
    return sorted[index];
}
} // namespace

int main(int argc, char **argv)
{
    if (argc < 2 || argc > 4)
    {
        std::fprintf(stderr, "usage: %s MODEL.rknn [iterations=100] [warmup=10]\n", argv[0]);
        return 2;
    }
    const int iterations = argc >= 3 ? std::atoi(argv[2]) : 100;
    const int warmup = argc >= 4 ? std::atoi(argv[3]) : 10;
    if (iterations <= 0 || warmup < 0)
    {
        std::fprintf(stderr, "iterations must be positive and warmup must be non-negative\n");
        return 2;
    }

    std::vector<unsigned char> model = read_file(argv[1]);
    if (model.empty())
    {
        std::fprintf(stderr, "failed to read model: %s\n", argv[1]);
        return 3;
    }

    rknn_context context = 0;
    int ret = rknn_init(&context, model.data(), static_cast<uint32_t>(model.size()), 0, nullptr);
    if (ret != RKNN_SUCC)
    {
        std::fprintf(stderr, "rknn_init failed: %d\n", ret);
        return 4;
    }

    rknn_input_output_num io_num{};
    ret = rknn_query(context, RKNN_QUERY_IN_OUT_NUM, &io_num, sizeof(io_num));
    if (ret != RKNN_SUCC || io_num.n_input != 1 || io_num.n_output == 0)
    {
        std::fprintf(stderr, "unsupported model I/O: ret=%d inputs=%u outputs=%u\n", ret, io_num.n_input,
                     io_num.n_output);
        rknn_destroy(context);
        return 5;
    }

    rknn_tensor_attr input_attr{};
    input_attr.index = 0;
    ret = rknn_query(context, RKNN_QUERY_INPUT_ATTR, &input_attr, sizeof(input_attr));
    if (ret != RKNN_SUCC)
    {
        std::fprintf(stderr, "failed to query input: %d\n", ret);
        rknn_destroy(context);
        return 5;
    }

    std::vector<unsigned char> input_data(input_attr.n_elems, 0);
    rknn_input input{};
    input.index = 0;
    input.buf = input_data.data();
    input.size = static_cast<uint32_t>(input_data.size());
    input.type = RKNN_TENSOR_UINT8;
    input.fmt = RKNN_TENSOR_NHWC;

    auto run_once = [&]() -> int {
        int status = rknn_inputs_set(context, 1, &input);
        if (status != RKNN_SUCC)
            return status;
        status = rknn_run(context, nullptr);
        if (status != RKNN_SUCC)
            return status;
        std::vector<rknn_output> outputs(io_num.n_output);
        for (uint32_t i = 0; i < io_num.n_output; ++i)
            outputs[i].index = i;
        status = rknn_outputs_get(context, io_num.n_output, outputs.data(), nullptr);
        if (status == RKNN_SUCC)
            status = rknn_outputs_release(context, io_num.n_output, outputs.data());
        return status;
    };

    for (int i = 0; i < warmup; ++i)
    {
        ret = run_once();
        if (ret != RKNN_SUCC)
        {
            std::fprintf(stderr, "warmup failed at iteration %d: %d\n", i, ret);
            rknn_destroy(context);
            return 6;
        }
    }

    std::vector<double> samples_ms;
    samples_ms.reserve(iterations);
    for (int i = 0; i < iterations; ++i)
    {
        const auto start = std::chrono::steady_clock::now();
        ret = run_once();
        const auto end = std::chrono::steady_clock::now();
        if (ret != RKNN_SUCC)
        {
            std::fprintf(stderr, "inference failed at iteration %d: %d\n", i, ret);
            rknn_destroy(context);
            return 7;
        }
        samples_ms.push_back(std::chrono::duration<double, std::milli>(end - start).count());
    }
    rknn_destroy(context);

    std::sort(samples_ms.begin(), samples_ms.end());
    const double mean_ms = std::accumulate(samples_ms.begin(), samples_ms.end(), 0.0) / samples_ms.size();
    std::printf("model=%s iterations=%d warmup=%d mean_ms=%.3f p50_ms=%.3f p95_ms=%.3f fps=%.2f\n", argv[1],
                iterations, warmup, mean_ms, percentile(samples_ms, 0.50), percentile(samples_ms, 0.95),
                1000.0 / mean_ms);
    return 0;
}
