# ARM64 wheels

将与板端 Python、RKNN Runtime 匹配的 RKNNLite wheel，以及对应 Python 的 NumPy ARM64 wheel 放在本目录后再构建镜像。

当前部署使用：

```text
rknn_toolkit_lite2-2.3.2-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
numpy-1.24.4-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
```

wheel 可从 [Rockchip RKNN-Toolkit2 v2.3.2](https://github.com/airockchip/rknn-toolkit2/releases/tag/v2.3.2) 获取。本目录中的 `*.whl` 被 Git 忽略。
