"""Bounded asynchronous adapter for a NON-causal TSE model.

2 s scheduled buffering is intentional and visible. This is a quality mode,
not a low-latency gaming claim. The audio pump never waits on neural inference.
Failures in this mode emit silence; unfiltered mixtures must never leak through.
"""
import queue
import threading
import time
import numpy as np

RATE = 16000
HOP = RATE
CONTEXT = 3200
DELAY = 2 * RATE


class TargetStream:
    def __init__(self, model, embedding):
        self.model = model
        self.embedding = np.array(embedding, dtype=np.float32, copy=True)
        self.overlap = getattr(model, 'supports_overlap', False) or getattr(model, 'device', 'cpu') == 'cuda'
        self.hop = 8000 if self.overlap else HOP
        self.left = int(getattr(model, 'stream_left', 8000)) if self.overlap else CONTEXT
        self.span = 16000
        self.right = int(getattr(model, 'stream_right', 8000)) if self.overlap else 0
        self.delay = int(getattr(model, 'stream_delay', DELAY))
        self.required = self.span + self.right
        self.previous_tail = None
        self.crossfade = np.sin(np.linspace(0, np.pi / 2, self.hop)) ** 2
        self.pending = np.empty(0, np.float32)
        self.context = np.zeros(self.left, np.float32)
        self.output = np.empty(0, np.float32)
        self.input_queue = queue.Queue(maxsize=2)
        self.output_queue = queue.Queue(maxsize=2)
        self.stop_event = threading.Event()
        self.elapsed = 0
        self.emitted = 0
        self.error = ''
        self.blocks = 0
        self.inference_ms = 0.
        self.input_rms = 0.
        self.extracted_rms = 0.
        self.low_output_blocks = 0
        # Finish first CUDA kernel setup before the microphone starts. It must
        # not consume the half-second deadline of the first context window.
        if self.overlap:
            model.extract(np.zeros(self.left + self.required, np.float32), self.embedding)
        self.thread = threading.Thread(target=self._work, name='target-speaker', daemon=True)
        self.thread.start()

    def _work(self):
        while not self.stop_event.is_set():
            try:
                x = self.input_queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                started = time.perf_counter()
                prediction = self.model.extract(x, self.embedding)[self.left:self.left+self.span]
                y = prediction[:self.hop].copy()
                if self.overlap:
                    if self.previous_tail is not None:
                        y = (self.previous_tail * (1-self.crossfade) + y * self.crossfade).astype(np.float32)
                    self.previous_tail = prediction[self.hop:].copy()
                self.input_rms = float(np.sqrt(np.mean(x[self.left:self.left+self.hop] ** 2)))
                self.extracted_rms = float(np.sqrt(np.mean(y ** 2)))
                if self.input_rms > .001 and self.extracted_rms < self.input_rms * .15:
                    self.low_output_blocks += 1
                else:
                    self.low_output_blocks = 0
                self.inference_ms = (time.perf_counter() - started) * 1000
                if self.stop_event.is_set():
                    break
                self.output_queue.put_nowait(y)
                self.blocks += 1
            except Exception as e:
                self.error = f'tse_processing_failed: {e}'
                self.stop_event.set()

    def process(self, frame):
        x = np.asarray(frame, np.float32)
        zero = np.zeros_like(x)
        if self.error or self.stop_event.is_set():
            return zero
        self.pending = np.concatenate((self.pending, x))
        if len(self.pending) >= self.required:
            request = np.concatenate((self.context, self.pending[:self.required]))
            self.context = np.concatenate((self.context, self.pending[:self.hop]))[-self.left:].copy()
            self.pending = self.pending[self.hop:]
            try:
                self.input_queue.put_nowait(request)
            except queue.Full:
                self.error = 'tse_too_slow'
                return zero
        elapsed_before = self.elapsed
        self.elapsed += len(x)
        padding = min(len(x), max(0, self.delay-elapsed_before))
        if padding == len(x):
            return zero
        needed = len(x)-padding
        if len(self.output) < needed:
            try:
                block = self.output_queue.get_nowait()
                self.output = np.concatenate((self.output, block))
            except queue.Empty:
                self.error = 'tse_too_slow'
                return zero
        y, self.output = self.output[:needed].copy(), self.output[needed:]
        # Tiny independent edge fades avoid a click at block boundaries.
        phase = self.emitted if self.overlap else self.emitted % HOP
        ramp = 80
        indexes = np.arange(len(y)) + phase
        gain = (np.minimum(1., indexes / ramp) if self.overlap else
                np.minimum(1., np.minimum(indexes / ramp, (HOP - 1 - indexes) / ramp)))
        y *= np.maximum(0., gain)
        self.emitted += needed
        if padding:
            zero[padding:] = y
            return zero
        return y

    def state(self):
        return {'enabled': True, 'bufferMs': self.delay / RATE * 1000,
                'contextMs': self.left / RATE * 1000, 'inferenceMs': round(self.inference_ms, 1),
                'overlap': self.overlap, 'hopMs': self.hop / RATE * 1000,
                'futureMs': (self.span + self.right - self.hop) / RATE * 1000,
                'blocks': self.blocks, 'error': self.error,
                'inputRms': self.input_rms, 'extractedRms': self.extracted_rms,
                'lowOutput': self.low_output_blocks >= 3,
                'verification': (self.model.verification_state()
                                 if hasattr(self.model, 'verification_state') else None),
                'warmingUp': self.elapsed <= self.delay,
                'device': getattr(self.model, 'device', 'cpu'), 'causal': False}

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('tse_worker_not_stopped')
