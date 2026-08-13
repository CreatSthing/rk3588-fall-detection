import argparse
from pathlib import Path

import numpy as np
import cv2
from rknn.api import RKNN

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ONNX_MODEL = PROJECT_ROOT / "assets" / "models" / "yolov5s.onnx"
DEFAULT_RKNN_MODEL = PROJECT_ROOT / "assets" / "weights" / "yolov5s_quant.rknn"
DEFAULT_IMG_PATH = PROJECT_ROOT / "assets" / "media" / "street.jpg"
DEFAULT_DATASET = PROJECT_ROOT / "assets" / "calibration" / "coco128_calibration.txt"
DEFAULT_RESULT = PROJECT_ROOT / "output" / "yolov5_convert" / "result.png"
DEFAULT_DUMP_DIR = PROJECT_ROOT / "output" / "yolov5_convert"

OBJ_THRESH = 0.25
NMS_THRESH = 0.45
CLASSES = ("person", "bicycle", "car", "motorbike ", "aeroplane ", "bus ", "train", "truck ", "boat", "traffic light",
           "fire hydrant", "stop sign ", "parking meter", "bench", "bird", "cat", "dog ", "horse ", "sheep", "cow", "elephant",
           "bear", "zebra ", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
           "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork", "knife ",
           "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza ", "donut", "cake", "chair", "sofa",
           "pottedplant", "bed", "diningtable", "toilet ", "tvmonitor", "laptop	", "mouse	", "remote ", "keyboard ", "cell phone", "microwave ",
           "oven ", "toaster", "sink", "refrigerator ", "book", "clock", "vase", "scissors ", "teddy bear ", "hair drier", "toothbrush ")


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def xywh2xyxy(x):
    # Convert [x, y, w, h] to [x1, y1, x2, y2]
    y = np.copy(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2  # top left x
    y[:, 1] = x[:, 1] - x[:, 3] / 2  # top left y
    y[:, 2] = x[:, 0] + x[:, 2] / 2  # bottom right x
    y[:, 3] = x[:, 1] + x[:, 3] / 2  # bottom right y
    return y


def process(input, mask, anchors, img_size):

    anchors = [anchors[i] for i in mask]
    grid_h, grid_w = map(int, input.shape[0:2])

    box_confidence = sigmoid(input[..., 4])
    box_confidence = np.expand_dims(box_confidence, axis=-1)

    box_class_probs = sigmoid(input[..., 5:])

    box_xy = sigmoid(input[..., :2])*2 - 0.5

    col = np.tile(np.arange(0, grid_w), grid_w).reshape(-1, grid_w)
    row = np.tile(np.arange(0, grid_h).reshape(-1, 1), grid_h)
    col = col.reshape(grid_h, grid_w, 1, 1).repeat(3, axis=-2)
    row = row.reshape(grid_h, grid_w, 1, 1).repeat(3, axis=-2)
    grid = np.concatenate((col, row), axis=-1)
    box_xy += grid
    box_xy *= int(img_size/grid_h)

    box_wh = pow(sigmoid(input[..., 2:4])*2, 2)
    box_wh = box_wh * anchors

    box = np.concatenate((box_xy, box_wh), axis=-1)

    return box, box_confidence, box_class_probs


def filter_boxes(boxes, box_confidences, box_class_probs):
    """Filter boxes with box threshold. It's a bit different with origin yolov5 post process!

    # Arguments
        boxes: ndarray, boxes of objects.
        box_confidences: ndarray, confidences of objects.
        box_class_probs: ndarray, class_probs of objects.

    # Returns
        boxes: ndarray, filtered boxes.
        classes: ndarray, classes for boxes.
        scores: ndarray, scores for boxes.
    """
    boxes = boxes.reshape(-1, 4)
    box_confidences = box_confidences.reshape(-1)
    box_class_probs = box_class_probs.reshape(-1, box_class_probs.shape[-1])

    _box_pos = np.where(box_confidences >= OBJ_THRESH)
    boxes = boxes[_box_pos]
    box_confidences = box_confidences[_box_pos]
    box_class_probs = box_class_probs[_box_pos]

    class_max_score = np.max(box_class_probs, axis=-1)
    classes = np.argmax(box_class_probs, axis=-1)
    _class_pos = np.where(class_max_score >= OBJ_THRESH)

    boxes = boxes[_class_pos]
    classes = classes[_class_pos]
    scores = (class_max_score* box_confidences)[_class_pos]

    return boxes, classes, scores


def nms_boxes(boxes, scores):
    """Suppress non-maximal boxes.

    # Arguments
        boxes: ndarray, boxes of objects.
        scores: ndarray, scores of objects.

    # Returns
        keep: ndarray, index of effective boxes.
    """
    x = boxes[:, 0]
    y = boxes[:, 1]
    w = boxes[:, 2] - boxes[:, 0]
    h = boxes[:, 3] - boxes[:, 1]

    areas = w * h
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)

        xx1 = np.maximum(x[i], x[order[1:]])
        yy1 = np.maximum(y[i], y[order[1:]])
        xx2 = np.minimum(x[i] + w[i], x[order[1:]] + w[order[1:]])
        yy2 = np.minimum(y[i] + h[i], y[order[1:]] + h[order[1:]])

        w1 = np.maximum(0.0, xx2 - xx1 + 0.00001)
        h1 = np.maximum(0.0, yy2 - yy1 + 0.00001)
        inter = w1 * h1

        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(ovr <= NMS_THRESH)[0]
        order = order[inds + 1]
    keep = np.array(keep)
    return keep


def _nms_by_class(boxes, classes, scores):
    nboxes, nclasses, nscores = [], [], []
    for class_id in set(classes):
        inds = np.where(classes == class_id)
        class_boxes = boxes[inds]
        class_ids = classes[inds]
        class_scores = scores[inds]
        keep = nms_boxes(class_boxes, class_scores)
        nboxes.append(class_boxes[keep])
        nclasses.append(class_ids[keep])
        nscores.append(class_scores[keep])

    if not nclasses:
        return None, None, None
    return np.concatenate(nboxes), np.concatenate(nclasses), np.concatenate(nscores)


def _decode_merged_output(output, img_size):
    """Decode a standard YOLOv5 ONNX output shaped [1, 25200, 85]."""
    predictions = np.asarray(output, dtype=np.float32)
    if predictions.ndim == 3 and predictions.shape[0] == 1:
        predictions = predictions[0]
    if predictions.ndim != 2 or predictions.shape[1] < 6:
        raise ValueError(
            "Expected a merged YOLOv5 output shaped [1, N, 5 + classes], "
            f"received {np.asarray(output).shape}."
        )

    objectness = predictions[:, 4]
    class_probs = predictions[:, 5:]
    # Standard YOLOv5 ONNX exports include Sigmoid. This also supports exports
    # that leave objectness and class confidence as logits.
    if (
        objectness.min() < 0
        or objectness.max() > 1
        or class_probs.min() < 0
        or class_probs.max() > 1
    ):
        objectness = sigmoid(objectness)
        class_probs = sigmoid(class_probs)

    classes = np.argmax(class_probs, axis=1)
    scores = objectness * class_probs[np.arange(len(predictions)), classes]
    keep = scores >= OBJ_THRESH
    if not np.any(keep):
        return None, None, None

    boxes = xywh2xyxy(predictions[keep, :4])
    boxes = np.clip(boxes, 0, img_size)
    return _nms_by_class(boxes, classes[keep], scores[keep])


def yolov5_post_process(input_data, img_size):
    masks = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    anchors = [[10, 13], [16, 30], [33, 23], [30, 61], [62, 45],
               [59, 119], [116, 90], [156, 198], [373, 326]]

    boxes, classes, scores = [], [], []
    for input, mask in zip(input_data, masks):
        b, c, s = process(input, mask, anchors, img_size)
        b, c, s = filter_boxes(b, c, s)
        boxes.append(b)
        classes.append(c)
        scores.append(s)

    boxes = np.concatenate(boxes)
    boxes = xywh2xyxy(boxes)
    classes = np.concatenate(classes)
    scores = np.concatenate(scores)

    return _nms_by_class(boxes, classes, scores)


def decode_yolov5_outputs(outputs, img_size):
    """Decode either three raw heads or a merged YOLOv5 ONNX output."""
    if len(outputs) == 1:
        return _decode_merged_output(outputs[0], img_size)

    input_data = []
    for output in outputs[:3]:
        output = np.asarray(output, dtype=np.float32)
        reshaped = output.reshape([3, -1] + list(output.shape[-2:]))
        input_data.append(np.transpose(reshaped, (2, 3, 0, 1)))
    return yolov5_post_process(input_data, img_size)


def draw(image, boxes, scores, classes):
    """Draw the boxes on the image.

    # Argument:
        image: original image.
        boxes: ndarray, boxes of objects.
        classes: ndarray, classes of objects.
        scores: ndarray, scores of objects.
        all_classes: all classes name.
    """
    for box, score, cl in zip(boxes, scores, classes):
        top, left, right, bottom = box
        print('class: {}, score: {}'.format(CLASSES[cl], score))
        print('box coordinate left,top,right,down: [{}, {}, {}, {}]'.format(top, left, right, bottom))
        top = int(top)
        left = int(left)
        right = int(right)
        bottom = int(bottom)

        cv2.rectangle(image, (top, left), (right, bottom), (255, 0, 0), 2)
        cv2.putText(image, '{0} {1:.2f}'.format(CLASSES[cl], score),
                    (top, left - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 0, 255), 2)


def letterbox(im, new_shape=(640, 640), color=(0, 0, 0)):
    # Resize and pad image while meeting stride-multiple constraints
    shape = im.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    # Scale ratio (new / old)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])

    # Compute padding
    ratio = r, r  # width, height ratios
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding

    dw /= 2  # divide padding into 2 sides
    dh /= 2

    if shape[::-1] != new_unpad:  # resize
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)  # add border
    return im, ratio, (dw, dh)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert YOLOv5 ONNX to RKNN, optionally quantize it, and run one demo inference."
    )
    parser.add_argument("--onnx", type=Path, default=DEFAULT_ONNX_MODEL, help="Input ONNX model.")
    parser.add_argument("--output", type=Path, default=DEFAULT_RKNN_MODEL, help="Output RKNN model.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Calibration dataset text file. Required when quantization is enabled.",
    )
    parser.add_argument("--image", type=Path, default=DEFAULT_IMG_PATH, help="Image used for demo inference.")
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT, help="Output image with detection boxes.")
    parser.add_argument("--dump-dir", type=Path, default=DEFAULT_DUMP_DIR, help="Directory for raw output .npy files.")
    parser.add_argument("--target", default="rk3588", help="RKNN target platform.")
    parser.add_argument("--input-name", default="images", help="ONNX input name.")
    parser.add_argument("--input-size", type=int, default=640, help="Square model input size.")
    quant_group = parser.add_mutually_exclusive_group(required=True)
    quant_group.add_argument(
        "--quantize",
        dest="quantize",
        action="store_true",
        help="Build an INT8 quantized RKNN model using --dataset.",
    )
    quant_group.add_argument(
        "--no-quant",
        dest="quantize",
        action="store_false",
        help="Build a floating-point RKNN model without calibration.",
    )
    parser.add_argument(
        "--skip-infer",
        action="store_true",
        help="Only convert/export the model; do not initialize runtime or run demo inference.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.onnx = args.onnx.resolve()
    args.output = args.output.resolve()
    args.dataset = args.dataset.resolve()
    args.image = args.image.resolve()
    args.result = args.result.resolve()
    args.dump_dir = args.dump_dir.resolve()

    if not args.onnx.exists():
        raise FileNotFoundError(f"ONNX model not found: {args.onnx}")
    if args.quantize and not args.dataset.exists():
        raise FileNotFoundError(
            f"INT8 quantization requires a calibration dataset: {args.dataset}"
        )
    if not args.skip_infer and not args.image.exists():
        raise FileNotFoundError(f"demo image not found: {args.image}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.dump_dir.mkdir(parents=True, exist_ok=True)

    print(f"Project root: {PROJECT_ROOT}")
    print(f"ONNX model: {args.onnx}")
    print(f"RKNN output: {args.output}")
    print(f"Quantization: {'INT8' if args.quantize else 'disabled (floating point)'}")
    if args.quantize:
        print(f"Calibration dataset: {args.dataset}")

    rknn = RKNN(verbose=True)
    try:
        print("--> Config model")
        ret = rknn.config(
            mean_values=[[0, 0, 0]],
            std_values=[[255, 255, 255]],
            target_platform=args.target,
        )
        if ret != 0:
            raise RuntimeError(f"rknn.config failed: {ret}")
        print("done")

        print("--> Loading model")
        ret = rknn.load_onnx(
            model=str(args.onnx),
            inputs=[args.input_name],
            input_size_list=[[1, 3, args.input_size, args.input_size]],
        )
        if ret != 0:
            raise RuntimeError(f"rknn.load_onnx failed: {ret}")
        print("done")

        print("--> Building model")
        ret = rknn.build(
            do_quantization=args.quantize,
            dataset=str(args.dataset) if args.quantize else None,
        )
        if ret != 0:
            raise RuntimeError(f"rknn.build failed: {ret}")
        print("done")

        print("--> Export RKNN model")
        ret = rknn.export_rknn(str(args.output))
        if ret != 0:
            raise RuntimeError(f"rknn.export_rknn failed: {ret}")
        print(f"exported: {args.output}")

        if args.skip_infer:
            return 0

        print("--> Init runtime environment")
        ret = rknn.init_runtime()
        if ret != 0:
            raise RuntimeError(f"rknn.init_runtime failed: {ret}")
        print("done")

        img = cv2.imread(str(args.image))
        if img is None:
            raise RuntimeError(f"OpenCV could not read image: {args.image}")
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (args.input_size, args.input_size))

        print("--> Running model")
        outputs = rknn.inference(inputs=[np.expand_dims(img, axis=0)])
        for index, output in enumerate(outputs):
            np.save(str(args.dump_dir / f"yolov5_{index}.npy"), output)
        print("done")

        boxes, classes, scores = decode_yolov5_outputs(outputs, args.input_size)
        result_image = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        if boxes is not None:
            draw(result_image, boxes, scores, classes)
        cv2.imwrite(str(args.result), result_image)
        print(f"result image: {args.result}")
    finally:
        rknn.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
