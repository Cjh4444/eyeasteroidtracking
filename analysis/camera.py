"""Scene-camera intrinsics and undistortion, from the Neon export's scene_camera.json."""
import json

import cv2
import numpy as np

from common import SCENE_CAM_JSON


def camera_params():
    cam = json.load(open(SCENE_CAM_JSON))
    K = np.array(cam["camera_matrix"], dtype=np.float64)
    # The Neon export carries 8 coefficients (OpenCV rational model). Pass all 8;
    # truncating to 5 leaves visible residual distortion at the frame edges.
    D = np.array(cam["distortion_coefficients"], dtype=np.float64).reshape(1, -1)
    return K, D


def undistort(pts, K, D):
    """Distorted pixel coords -> undistorted pixel coords (same K)."""
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.undistortPoints(p, K, D, P=K).reshape(-1, 2)
