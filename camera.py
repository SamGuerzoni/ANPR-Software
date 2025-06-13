import cv2

class VideoCamera:
    def __init__(self, rtsp_url):
        self.video = cv2.VideoCapture(rtsp_url)

    def __del__(self):
        if self.video.isOpened():
            self.video.release()

    def get_frame(self):
        success, image = self.video.read()
        if success:
            ret, jpeg = cv2.imencode('.jpg', image)
            return jpeg.tobytes()
        return None
