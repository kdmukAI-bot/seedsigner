# import the necessary packages
import logging
from picamera.array import PiRGBArray
from picamera import PiCamera
from threading import Thread
import time

from seedsigner.models.bench_stats import BENCH

logger = logging.getLogger(__name__)


# Modified from: https://github.com/jrosebr1/imutils
class PiVideoStream:
	def __init__(self, resolution=(320, 240), framerate=32, format="bgr", **kwargs):
		# initialize the camera
		self.camera = PiCamera(resolution=resolution, framerate=framerate, **kwargs)

		# initialize the stream
		self.rawCapture = PiRGBArray(self.camera, size=resolution)
		self.stream = self.camera.capture_continuous(self.rawCapture,
			format=format, use_video_port=True)

		# initialize the frame and the variable used to indicate
		# if the thread should be stopped
		self.frame = None
		self.should_stop = False
		self.is_stopped = True

		# Capture sequence number, incremented once per frame the camera delivers. Held
		# together with its frame in a single tuple so a reader cannot pair one capture's
		# number with another capture's pixels. Consumers read `latest` to tell a fresh
		# delivery from the same frame served twice; `read()` is unaffected.
		self.frame_seq = 0
		self.latest = (0, None)

	def start(self):
		# start the thread to read frames from the video stream
		t = Thread(target=self.update, args=())
		t.daemon = True
		t.start()
		self.is_stopped = False
		return self

	def update(self):
		# keep looping infinitely until the thread is stopped
		for f in self.stream:
			# grab the frame from the stream and clear the stream in
			# preparation for the next frame
			self.frame = f.array
			self.frame_seq += 1
			self.latest = (self.frame_seq, self.frame)
			BENCH.capture_frames += 1
			self.rawCapture.truncate(0)

			# if the thread indicator variable is set, stop the thread
			# and release camera resources
			if self.should_stop:
				logger.info("PiVideoStream: closing everything")
				self.stream.close()
				self.rawCapture.close()
				self.camera.close()
				self.should_stop = False
				self.is_stopped = True
				return

	def read(self):
		# return the frame most recently read
		return self.frame

	def stop(self):
		# indicate that the thread should be stopped
		self.should_stop = True

		# Block in this thread until stopped
		while not self.is_stopped:
			pass
