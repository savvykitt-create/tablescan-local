"""Isolated MLX or PyTorch process. Reads pixels and prompts only; never annotations.

Kept as a standalone script so the Qt/ONNX app never loads Metal/CUDA libraries.
"""
import json
import os
import sys
import time
from pathlib import Path


def prepare_record(record, kind):
    from PIL import Image, ImageOps
    with Image.open(record['image']) as source:
        image = ImageOps.expand(source.convert('RGB'), border=12, fill='white')
    if kind == 'qwen':
        task = (f'Transcribe this table exactly. The left margin contains row positions 1 through {record["rows"]}. '
                f'Return only a JSON array with one object for every row: {{"row": integer, "values": [{record["columns"]} strings or null]}}. '
                f'Each values array must contain exactly {record["columns"]} handwritten measurements in left-to-right order. '
                'Include blank and crossed-out rows using null values. Do not infer or correct digits. Do not omit any rows.')
        return image, task, 4096
    image = image.resize((round(image.width * 144 / image.height), 144), Image.Resampling.LANCZOS)
    return image, 'Text Recognition:', 192


def choose_device(torch, requested, kind):
    if requested not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('Unknown device')
    if requested == 'cpu':
        return 'cpu'
    available = torch.cuda.is_available()
    if requested == 'cuda':
        if not available:
            raise RuntimeError('NVIDIA CUDA is unavailable. Install the CUDA runtime or choose CPU.')
        return 'cuda'
    # Avoid moving a large model onto a GPU that cannot hold weights + image context.
    if available:
        free, _ = torch.cuda.mem_get_info()
        if free >= (12 if kind == 'qwen' else 4) * 1024**3:
            return 'cuda'
    return 'cpu'


def write_status(request, device, phase):
    if request.get('status'):
        path = Path(request['status'])
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'device': device, 'phase': phase}), encoding='utf-8')
        temporary.replace(path)


class TransformersEngine:
    def __init__(self, request, device=None):
        import torch
        from transformers import AutoProcessor, AutoModelForImageTextToText
        self.torch = torch
        self.device = device or choose_device(torch, request.get('device', 'auto'), request['kind'])
        # BF16 retains the published weights and halves RAM use. Float32 is an explicit compatibility option.
        self.dtype = (getattr(torch, request.get('cpu_dtype', 'bfloat16')) if self.device == 'cpu' else
                      torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
        if self.device == 'cpu':
            torch.set_num_threads(max(1, min(8, (os.cpu_count() or 2) // 2)))
        write_status(request, self.device, 'loading')
        self.processor = AutoProcessor.from_pretrained(request['model'], local_files_only=True, trust_remote_code=False)
        self.model = AutoModelForImageTextToText.from_pretrained(
            request['model'], local_files_only=True, trust_remote_code=False,
            dtype=self.dtype, attn_implementation='sdpa').to(self.device).eval()
        self.execution = {'backend': 'transformers', 'device': self.device, 'dtype': str(self.dtype),
                          'torch': torch.__version__}
        print(json.dumps(self.execution), flush=True)

    def generate(self, image, task, limit):
        messages = [{'role': 'user', 'content': [{'type': 'image', 'image': image},
                                                 {'type': 'text', 'text': task}]}]
        print('Preparing image and prompt', flush=True)
        inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors='pt', enable_thinking=False)
        print('Moving input tensors to ' + self.device, flush=True)
        inputs = inputs.to(self.device)
        # Token IDs and grid dimensions must remain integers.
        for key, value in inputs.items():
            if self.torch.is_floating_point(value):
                inputs[key] = value.to(self.dtype)
        print('Generating tokens', flush=True)
        with self.torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=limit, do_sample=False, use_cache=True)
        if self.device == 'cuda':
            self.torch.cuda.synchronize()
        return self.processor.decode(output[0, inputs['input_ids'].shape[-1]:], skip_special_tokens=True)


class MlxEngine:
    def __init__(self, request):
        import mlx.core as mx
        from mlx_vlm import load
        self.mx = mx
        write_status(request, 'metal', 'loading')
        self.model, self.processor = load(request['model'])
        self.execution = {'backend': 'mlx', 'device': 'metal'}

    def generate(self, image, task, limit):
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        prompt = apply_chat_template(self.processor, self.model.config, task, num_images=1, enable_thinking=False)
        result = generate(self.model, self.processor, prompt, image=[image], max_tokens=limit, temperature=0, verbose=False)
        self.mx.synchronize()
        return result.text


def execute(request, output_path, device=None):
    engine = MlxEngine(request) if request.get('backend', 'mlx') == 'mlx' else TransformersEngine(request, device)
    write_status(request, engine.execution['device'], 'inference')
    records = []
    for record in request['records']:
        image, task, limit = prepare_record(record, request['kind'])
        start = time.monotonic()
        raw = engine.generate(image, task, limit)
        records.append({'id': record['id'], 'raw': raw, 'seconds': time.monotonic() - start,
                        'execution': engine.execution})
        temporary = output_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
        temporary.replace(output_path)


def main():
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    request_path, output_path = map(Path, sys.argv[1:3])
    request = json.loads(request_path.read_text(encoding='utf-8'))
    if request.get('backend', 'mlx') not in {'mlx', 'transformers'}:
        raise ValueError('Unknown backend')
    retry_cpu = False
    try:
        execute(request, output_path)
    except Exception as exc:
        if request.get('backend') == 'transformers' and request.get('device', 'auto') == 'auto':
            import torch
            retry_cpu = isinstance(exc, torch.cuda.OutOfMemoryError)
        if not retry_cpu:
            raise
    if retry_cpu:
        # Leave the exception scope before collecting tensors retained by its traceback.
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()
        output_path.unlink(missing_ok=True)
        print('CUDA memory exhausted; retrying on CPU.', flush=True)
        execute(request, output_path, device='cpu')


if __name__ == '__main__':
    main()
