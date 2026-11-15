"""The ONNX classifier for prompt injection. It runs on the CPU.

Model: protectai/deberta-v3-base-prompt-injection-v2 (Apache-2.0). Labels: 0 SAFE, 1 INJECTION.
Long text is cut into windows. The score is the highest window score.
The heavy imports happen inside load(), so the rest of the code works without them.
"""
import math
import pathlib

MAX_TOKENS = 510            # the model takes 512 including [CLS] and [SEP]
STRIDE = 384                # windows overlap, so an attack cannot hide at a cut
BATCH = 4


def chunk_ids(ids, size=MAX_TOKENS, stride=STRIDE):
    """Cut a token list into overlapping windows. An empty list gives one empty window."""
    if len(ids) <= size:
        return [ids]
    windows = []
    start = 0
    while True:
        windows.append(ids[start:start + size])
        if start + size >= len(ids):
            break
        start += stride
    return windows


def softmax_pair(a, b):
    m = max(a, b)
    ea, eb = math.exp(a - m), math.exp(b - m)
    return eb / (ea + eb)


class OnnxClassifier:
    def __init__(self, model_dir, threads=2):
        self.model_dir = pathlib.Path(model_dir)
        self.threads = threads
        self.session = None
        self.tokenizer = None

    def load(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        opts.inter_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(self.model_dir / "model.onnx"), opts, providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.cls_id = self.tokenizer.token_to_id("[CLS]")
        self.sep_id = self.tokenizer.token_to_id("[SEP]")
        self.pad_id = self.tokenizer.token_to_id("[PAD]") or 0
        self.input_names = {i.name for i in self.session.get_inputs()}
        return self

    def injection_score(self, text):
        import numpy as np
        ids = self.tokenizer.encode(text or "", add_special_tokens=False).ids
        windows = chunk_ids(ids)
        best = 0.0
        for i in range(0, len(windows), BATCH):
            batch = [[self.cls_id] + w + [self.sep_id] for w in windows[i:i + BATCH]]
            width = max(len(b) for b in batch)
            input_ids = np.full((len(batch), width), self.pad_id, dtype=np.int64)
            mask = np.zeros((len(batch), width), dtype=np.int64)
            for r, row in enumerate(batch):
                input_ids[r, :len(row)] = row
                mask[r, :len(row)] = 1
            feed = {"input_ids": input_ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(input_ids)
            logits = self.session.run(None, feed)[0]
            for row in logits:
                best = max(best, softmax_pair(float(row[0]), float(row[1])))
        return best
