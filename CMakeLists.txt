# 设置最低版本号
cmake_minimum_required(VERSION 3.11 FATAL_ERROR)
# 设置项目名称
project(rk3588-demo VERSION 0.0.1 LANGUAGES CXX)

# 输出系统信息
message(STATUS "System: ${CMAKE_SYSTEM_NAME} ${CMAKE_SYSTEM_VERSION}")

# 设置编译器
set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(BUILD_RK3588_TARGETS "Build RKNN/MPP targets for RK3588" ON)
option(BUILD_PC_YOLO_DEMO "Build the OpenCV-DNN PC test program" OFF)

find_package(OpenCV REQUIRED)

set(PROJECT_SRC_DIR ${CMAKE_CURRENT_SOURCE_DIR}/src)

set(PC_YOLO_SOURCES
    src/pc/pc_yolov5.cpp
    apps/pc_yolov5/main.cpp
    src/draw/cv_draw.cpp
)

if(BUILD_PC_YOLO_DEMO)
    add_executable(pc_yolov5 ${PC_YOLO_SOURCES})
    target_include_directories(pc_yolov5 PRIVATE
        ${OpenCV_INCLUDE_DIRS}
        ${PROJECT_SRC_DIR}
    )
    target_link_libraries(pc_yolov5 ${OpenCV_LIBS})
endif()

if(NOT BUILD_RK3588_TARGETS)
    return()
endif()

# 设置库架构
set(LIB_ARCH "aarch64")
set(DEVICE_NAME "RK3588")

# 
link_directories(
    mpp_libs
)
#  rknn_api 文件夹路径
set(RKNN_API_PATH ${CMAKE_CURRENT_SOURCE_DIR}/librknn_api)
#  rknn_api include 路径
set(RKNN_API_INCLUDE_PATH ${RKNN_API_PATH}/include)
#  rknn_api lib 路径
set(RKNN_API_LIB_PATH ${RKNN_API_PATH}/${LIB_ARCH}/librknnrt.so)

# 寻找OpenCV库，使用自定义的OpenCV_DIR
set(3RDPARTY_PATH ${CMAKE_CURRENT_SOURCE_DIR}/3rdparty)
set(RGA_DIR ${3RDPARTY_PATH}/rga/${DEVICE_NAME})
set(RGA_LIB ${RGA_DIR}/lib/Linux/${LIB_ARCH}/librga.so)
# set(OpenCV_DIR ${3RDPARTY_PATH}/opencv/opencv-linux-${LIB_ARCH}/share/OpenCV)
# 输出OpenCV信息
message(STATUS "include path: ${OpenCV_INCLUDE_DIRS}")

set(RK3588_COMMON_INCLUDE_DIRS
    ${OpenCV_INCLUDE_DIRS}
    ${RKNN_API_INCLUDE_PATH}
    ${PROJECT_SRC_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/submodules/ZLMediaKit/api/include
    ${RGA_DIR}/include
    ${CMAKE_CURRENT_LIST_DIR}/submodules/mpp/inc
    ${CMAKE_CURRENT_LIST_DIR}/submodules/mpp/osal/inc
    ${CMAKE_CURRENT_LIST_DIR}/submodules/mpp/utils
    ${CMAKE_CURRENT_LIST_DIR}/submodules/mpp/mpp/inc
    ${CMAKE_CURRENT_LIST_DIR}/submodules/mpp/mpp/base/inc
)

set(NN_PROCESS_SOURCES
    src/process/preprocess.cpp
    src/process/yolov5_postprocess.cpp
)

set(STREAM_MEDIA_SOURCES
    src/media/media_buffer.cpp
    src/media/mpi_enc.cpp
    src/media/mpi_dec.cpp
    src/media/ffmpeg_source.cpp
    src/media/zlmedia_worker.cpp
)

set(STREAM_PLATFORM_LIBS
    rockchip_mpp # 在mpp_libs下，包含了所有的mpp库
    utils # 在mpp_libs下，MPP工具库
    mk_api # 在mpp_libs下，ZLMediaKit API库
    avformat
    avcodec
    avutil
)


# 用来搜索头文件的目录
include_directories(${RK3588_COMMON_INCLUDE_DIRS})

# 构建预处理和后处理库
add_library(nn_process SHARED ${NN_PROCESS_SOURCES})
# 链接库
target_link_libraries(nn_process
    ${OpenCV_LIBS}
    ${RGA_LIB}
)

# 构建自定义封装API库
add_library(rknn_engine SHARED src/engine/rknn_engine.cpp) 
# 链接库
target_link_libraries(rknn_engine
    ${RKNN_API_LIB_PATH}
)
# yolov5_lib
add_library(yolov5_lib SHARED src/yolov5/yolov5.cpp)
# 链接库
target_link_libraries(yolov5_lib
    rknn_engine
    nn_process
)

# draw_lib
add_library(draw_lib SHARED src/draw/cv_draw.cpp)
# 链接库
target_link_libraries(draw_lib
    ${OpenCV_LIBS}
)

# 测试自yolov5
add_executable(yolov5_img apps/image_demo/main.cpp)

# 链接库
target_link_libraries(yolov5_img
        draw_lib
        yolov5_lib
)

# 测试自yolov5 video
add_executable(yolov5_video apps/video_demo/main.cpp)

# 链接库
target_link_libraries(yolov5_video
        draw_lib
        yolov5_lib
)

# 测试自yolov5 thread pool
add_executable(yolov5_thread_pool 
    apps/thread_pool_demo/main.cpp
    src/yolov5/yolov5_thread_pool.cpp
    )

# 链接库
target_link_libraries(yolov5_thread_pool
        draw_lib
        yolov5_lib
)

# 测试yolov5_stream
add_executable(yolov5_stream
        apps/stream_demo/main.cpp
        ${STREAM_MEDIA_SOURCES}
)

target_link_libraries(yolov5_stream
        ${STREAM_PLATFORM_LIBS}
        draw_lib
        yolov5_lib
)


# 测试yolov5_stream_pool
add_executable(yolov5_stream_pool
        apps/stream_pipeline/main.cpp
        src/pipeline/video_analytics_pipeline.cpp
        src/yolov5/yolov5_thread_pool.cpp
        ${STREAM_MEDIA_SOURCES}
)

target_link_libraries(yolov5_stream_pool
        ${STREAM_PLATFORM_LIBS}
        draw_lib
        yolov5_lib
)
