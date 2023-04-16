import threading

import imgviz
import numpy as np
import onnxruntime
import PIL.Image
import skimage.measure


class SegmentAnythingModel:
    def __init__(self):
        self._image_size = 1024

        # encoder_path = "../segment-anything/models/sam_vit_h_4b8939.quantized.encoder.onnx"  # NOQA
        # decoder_path = "../segment-anything/models/sam_vit_h_4b8939.quantized.decoder.onnx"  # NOQA
        encoder_path = "../segment-anything/models/sam_vit_l_0b3195.quantized.encoder.onnx"  # NOQA
        decoder_path = "../segment-anything/models/sam_vit_l_0b3195.quantized.decoder.onnx"  # NOQA
        # encoder_path = "../segment-anything/models/sam_vit_b_01ec64.quantized.encoder.onnx"  # NOQA
        # decoder_path = "../segment-anything/models/sam_vit_b_01ec64.quantized.decoder.onnx"  # NOQA

        self._encoder_session = onnxruntime.InferenceSession(encoder_path)
        self._decoder_session = onnxruntime.InferenceSession(decoder_path)

    def set_image(self, image: np.ndarray):
        self._image = image
        self._image_embedding = None

        self._thread = threading.Thread(target=self.get_image_embedding)
        self._thread.start()

    def get_image_embedding(self):
        if self._image_embedding is None:
            self._image_embedding = compute_image_embedding(
                image_size=self._image_size,
                encoder_session=self._encoder_session,
                image=self._image,
            )
        return self._image_embedding

    def points_to_polygon_callback(self, points):
        self._thread.join()
        image_embedding = self.get_image_embedding()

        polygon = compute_polygon_from_points(
            image_size=self._image_size,
            decoder_session=self._decoder_session,
            image=self._image,
            image_embedding=image_embedding,
            points=points,
        )
        return polygon


def compute_image_embedding(image_size, encoder_session, image):
    assert image.shape[1] > image.shape[0]
    scale = image_size / image.shape[1]
    x = imgviz.resize(
        image,
        height=int(round(image.shape[0] * scale)),
        width=image_size,
        backend="pillow",
    ).astype(np.float32)
    x = (x - np.array([123.675, 116.28, 103.53], dtype=np.float32)) / np.array(
        [58.395, 57.12, 57.375], dtype=np.float32
    )
    x = np.pad(
        x,
        (
            (0, image_size - x.shape[0]),
            (0, image_size - x.shape[1]),
            (0, 0),
        ),
    )
    x = x.transpose(2, 0, 1)[None, :, :, :]

    output = encoder_session.run(output_names=None, input_feed={"x": x})
    image_embedding = output[0]

    return image_embedding


def _get_contour_length(contour):
    contour_start = contour
    contour_end = np.r_[contour[1:], contour[0:1]]
    return np.linalg.norm(contour_end - contour_start, axis=1).sum()


def compute_polygon_from_points(
    image_size, decoder_session, image, image_embedding, points
):
    input_point = np.array(points, dtype=np.float32)
    input_label = np.ones(len(input_point), dtype=np.int32)

    onnx_coord = np.concatenate([input_point, np.array([[0.0, 0.0]])], axis=0)[
        None, :, :
    ]
    onnx_label = np.concatenate([input_label, np.array([-1])], axis=0)[
        None, :
    ].astype(np.float32)

    assert image.shape[1] > image.shape[0]
    scale = image_size / image.shape[1]
    new_height = int(round(image.shape[0] * scale))
    new_width = image_size
    onnx_coord = (
        onnx_coord.astype(float)
        * (new_width / image.shape[1], new_height / image.shape[0])
    ).astype(np.float32)

    onnx_mask_input = np.zeros((1, 1, 256, 256), dtype=np.float32)
    onnx_has_mask_input = np.array([-1], dtype=np.float32)

    decoder_inputs = {
        "image_embeddings": image_embedding,
        "point_coords": onnx_coord,
        "point_labels": onnx_label,
        "mask_input": onnx_mask_input,
        "has_mask_input": onnx_has_mask_input,
        "orig_im_size": np.array(image.shape[:2], dtype=np.float32),
    }

    masks, _, _ = decoder_session.run(None, decoder_inputs)
    mask = masks[0, 0]  # (1, 1, H, W) -> (H, W)
    mask = mask > 0.0
    if 0:
        imgviz.io.imsave(
            "mask.jpg", imgviz.label2rgb(mask, imgviz.rgb2gray(image))
        )

    contours = skimage.measure.find_contours(mask)
    contour = max(contours, key=_get_contour_length)
    polygon = skimage.measure.approximate_polygon(
        coords=contour,
        tolerance=np.ptp(contour, axis=0).max() / 100,
    )
    if 0:
        image_pil = PIL.Image.fromarray(image)
        imgviz.draw.line_(image_pil, yx=polygon, fill=(0, 255, 0))
        for point in polygon:
            imgviz.draw.circle_(
                image_pil, center=point, diameter=10, fill=(0, 255, 0)
            )
        imgviz.io.imsave("contour.jpg", np.asarray(image_pil))

    return polygon[:, ::-1]  # yx -> xy
