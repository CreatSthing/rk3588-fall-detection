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
option(BUILD_STREAM_TARGETS "Build MPP/ZLMediaKit streaming targets" OFF)
option(USE_SYSTEM_RGA "Use the board's RGA runtime to match its media stack" ON)
option(USE_MANUAL_OPENCV "Use manually specified OpenCV include/libs for cross builds" OFF)

if(USE_MANUAL_OPENCV)
    set(OpenCV_INCLUDE_DIRS /usr/include/opencv4)
    set(OpenCV_LIBS opencv_core opencv_imgproc opencv_imgcodecs opencv_videoio)
else()
    find_package(OpenCV REQUIRED)
endif()
find_package(Threads REQUIRED)

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
if(BUILD_STREAM_TARGETS)
    link_directories(mpp_libs)
endif()
#  rknn_api 文件夹路径
set(RKNN_API_PATH ${CMAKE_CURRENT_SOURCE_DIR}/librknn_api)
#  rknn_api include 路径
set(RKNN_API_INCLUDE_PATH ${RKNN_API_PATH}/include)
#  rknn_api lib 路径
set(RKNN_API_LIB_PATH ${RKNN_API_PATH}/${LIB_ARCH}/librknnrt.so)

# 寻找OpenCV库，使用自定义的OpenCV_DIR
set(3RDPARTY_PATH ${CMAKE_CURRENT_SOURCE_DIR}/3rdparty)
set(RGA_DIR ${3RDPARTY_PATH}/rga/${DEVICE_NAME})
set(ROCKCHIP_INCLUDE_DIR ${3RDPARTY_PATH}/rockchip/include)
set(FFMPEG_AARCH64_INCLUDE_DIR ${3RDPARTY_PATH}/ffmpeg/aarch64/include)
set(FFMPEG_AARCH64_LIB_DIR ${3RDPARTY_PATH}/ffmpeg/aarch64/lib)
if(BUILD_STREAM_TARGETS AND EXISTS "${FFMPEG_AARCH64_LIB_DIR}")
    link_directories(${FFMPEG_AARCH64_LIB_DIR})
endif()
if(USE_SYSTEM_RGA)
    find_library(RGA_LIB NAMES rga REQUIRED)
else()
    set(RGA_LIB ${RGA_DIR}/lib/Linux/${LIB_ARCH}/librga.so)
endif()
# set(OpenCV_DIR ${3RDPARTY_PATH}/opencv/opencv-linux-${LIB_ARCH}/share/OpenCV)
# 输出OpenCV信息
message(STATUS "include path: ${OpenCV_INCLUDE_DIRS}")

set(RK3588_COMMON_INCLUDE_DIRS
    ${OpenCV_INCLUDE_DIRS}
    ${RKNN_API_INCLUDE_PATH}
    ${PROJECT_SRC_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/submodules/ZLMediaKit/api/include
    ${RGA_DIR}/include
    ${FFMPEG_AARCH64_INCLUDE_DIR}
    /usr/include/aarch64-linux-gnu
    /usr/include/rga
    /usr/include/rockchip
    ${ROCKCHIP_INCLUDE_DIR}
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

set(MPP_RGA_PROBE_LIBS
    rockchip_mpp
    avformat
    avcodec
    avutil
    ${RGA_LIB}
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

add_executable(rknn_benchmark tools/benchmark_rknn_model.cpp)
target_link_libraries(rknn_benchmark ${RKNN_API_LIB_PATH})
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
        opencv_videoio
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
        opencv_videoio
        Threads::Threads
)

# 测试yolov5_stream
if(BUILD_STREAM_TARGETS)
    if(NOT EXISTS "/usr/include/rockchip/rk_mpi.h" AND
       NOT EXISTS "${ROCKCHIP_INCLUDE_DIR}/rk_mpi.h" AND
       NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/submodules/mpp/mpp/inc/rk_mpi.h")
        message(FATAL_ERROR "BUILD_STREAM_TARGETS requires Rockchip MPP headers")
    endif()

    add_executable(mpp_dma_rga_probe apps/mpp_dma_rga_probe/main.cpp)
    target_link_libraries(mpp_dma_rga_probe ${MPP_RGA_PROBE_LIBS})

    add_executable(mpp_rga_thread_pool
            apps/mpp_rga_thread_pool/main.cpp
            src/yolov5/yolov5_thread_pool.cpp
    )
    target_link_libraries(mpp_rga_thread_pool
            rockchip_mpp
            ${RGA_LIB}
            avformat
            avcodec
            avutil
            draw_lib
            yolov5_lib
            Threads::Threads
    )

    if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/submodules/ZLMediaKit/api/include/mk_mediakit.h")
        message(WARNING "ZLMediaKit headers are missing; yolov5_stream targets will be skipped")
        set(BUILD_ZLMEDIAKIT_STREAM_TARGETS OFF)
    else()
        set(BUILD_ZLMEDIAKIT_STREAM_TARGETS ON)
    endif()
endif()

if(BUILD_STREAM_TARGETS AND BUILD_ZLMEDIAKIT_STREAM_TARGETS)

    add_executable(yolov5_stream
            apps/stream_demo/main.cpp
            ${STREAM_MEDIA_SOURCES}
    )
    target_link_libraries(yolov5_stream
            ${STREAM_PLATFORM_LIBS}
            draw_lib
            yolov5_lib
    )

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
endif()

set(RK3588_INSTALL_TARGETS rknn_benchmark yolov5_img yolov5_video yolov5_thread_pool)
if(TARGET mpp_dma_rga_probe)
    list(APPEND RK3588_INSTALL_TARGETS mpp_dma_rga_probe)
endif()
if(TARGET mpp_rga_thread_pool)
    list(APPEND RK3588_INSTALL_TARGETS mpp_rga_thread_pool)
endif()

set_target_properties(${RK3588_INSTALL_TARGETS}
    PROPERTIES INSTALL_RPATH "$ORIGIN/../lib")
set_target_properties(nn_process rknn_engine yolov5_lib draw_lib
    PROPERTIES INSTALL_RPATH "$ORIGIN")

install(TARGETS ${RK3588_INSTALL_TARGETS}
        RUNTIME DESTINATION bin)
install(TARGETS nn_process rknn_engine yolov5_lib draw_lib
        LIBRARY DESTINATION lib)
install(FILES ${RKNN_API_LIB_PATH} DESTINATION lib)
if(NOT USE_SYSTEM_RGA)
    install(FILES ${RGA_LIB} DESTINATION lib)
endif()
install(PROGRAMS tools/probe_mpp_dma_rga.sh DESTINATION bin)
