"""Isolated MLX process. Reads pixels and prompts only; never annotations.

Kept as a standalone script so the Qt/ONNX app never loads Metal libraries.
"""
import json
import os
import sys
import time
from pathlib import Path


def main():
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    from PIL import Image, ImageOps
    import mlx.core as mx
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template

    request_path, output_path = map(Path, sys.argv[1:3])
    request = json.loads(request_path.read_text())
    model, processor = load(request['model'])
    records = []
    for record in request['records']:
        with Image.open(record['image']) as source:
            im = ImageOps.expand(source.convert('RGB'), border=12, fill='white')
        task = 'Text Recognition:'
        limit = 192
        if request['kind'] == 'qwen':
            task = (f'Transcribe this table exactly. The left margin contains row positions 1 through {record["rows"]}. '
                    f'Return only a JSON array with one object for every row: {{"row": integer, "values": [{record["columns"]} strings or null]}}. '
                    f'Each values array must contain exactly {record["columns"]} handwritten measurements in left-to-right order. '
                    'Include blank and crossed-out rows using null values. Do not infer or correct digits. Do not omit any rows.')
            limit = 4096
        else:
            im = im.resize((round(im.width * 144 / im.height), 144), Image.Resampling.LANCZOS)
        prompt = apply_chat_template(processor, model.config, task, num_images=1, enable_thinking=False)
        start = time.monotonic()
        result = generate(model, processor, prompt, image=[im], max_tokens=limit, temperature=0, verbose=False)
        mx.synchronize()
        records.append({'id': record['id'], 'raw': result.text, 'seconds': time.monotonic() - start})
        # Atomic snapshots allow the host to display progress without pipes.
        temporary = output_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, ensure_ascii=False))
        temporary.replace(output_path)


if __name__ == '__main__':
    main()
