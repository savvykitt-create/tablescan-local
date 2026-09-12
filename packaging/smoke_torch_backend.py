"""Exercise real multimodal CPU kernels with tiny random models, not OCR accuracy.

Downloads only the pinned processors/configs, never production weights. Run in
an isolated PyTorch environment so the desktop bundle cannot collect torch.
"""
import gc
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from PIL import Image, ImageDraw
from transformers import AutoConfig, AutoModelForImageTextToText

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'src/tablescan_local' / f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    runtime, runner = module('slow_runtime'), module('slow_runner')
    torch.set_num_threads(2)
    torch.manual_seed(7)
    print('CPU capability: ' + torch.backends.cpu.get_cpu_capability(), flush=True)
    with tempfile.TemporaryDirectory(prefix='tablescan-cpu-kernels-') as directory:
        root = Path(directory)
        for kind, (repo, revision) in runtime.MODEL_SETS['transformers'].items():
            folder = root / kind
            snapshot_download(repo, revision=revision, local_dir=folder,
                allow_patterns=['*.json', '*.txt', '*.jinja', '*.model', '*.tiktoken'])
            config = AutoConfig.from_pretrained(folder, local_files_only=True)
            text = config.text_config
            text.hidden_size = 128
            text.intermediate_size = 256
            text.num_hidden_layers = 2
            text.num_attention_heads = 2
            text.num_key_value_heads = 1
            text.head_dim = 64
            text.rope_parameters['mrope_section'] = [4, 6, 6] if kind == 'qwen' else [8, 12, 12]
            text.rope_parameters['partial_rotary_factor'] = .5 if kind == 'qwen' else 1.0
            if kind == 'qwen':
                text.layer_types = ['linear_attention', 'full_attention']
                text.linear_num_key_heads = 2
                text.linear_num_value_heads = 4
                text.linear_key_head_dim = text.linear_value_head_dim = 16
            vision = config.vision_config
            vision.depth = 1
            vision.hidden_size = 128
            vision.intermediate_size = 256
            vision.num_heads = 4
            vision.out_hidden_size = text.hidden_size
            model = AutoModelForImageTextToText.from_config(config, attn_implementation='sdpa').to(torch.bfloat16)
            model.save_pretrained(folder)
            # Discard the full model's shard index, downloaded with its processor metadata.
            (folder / 'model.safetensors.index.json').unlink(missing_ok=True)
            del model
            gc.collect()
            image = Image.new('RGB', (260, 80), 'white')
            ImageDraw.Draw(image).text((12, 24), '12.3  45.6', fill='black')
            path = root / 'измерения.png'; image.save(path)
            image, task, _ = runner.prepare_record({'image': str(path), 'rows': 1, 'columns': 2}, kind)
            os.environ['HF_HUB_OFFLINE'] = '1'
            engine = runner.TransformersEngine({'kind': kind, 'model': str(folder), 'device': 'cpu'})
            result = engine.generate(image, task, 2)
            assert isinstance(result, str)
            assert engine.device == 'cpu' and engine.dtype == torch.bfloat16
            assert all(torch.isfinite(parameter).all() for parameter in engine.model.parameters())
            print(json.dumps({'kind': kind, 'cpu_multimodal_generation': 'passed', 'dtype': 'bfloat16'}), flush=True)
            del engine
            gc.collect()
            os.environ.pop('HF_HUB_OFFLINE', None)


if __name__ == '__main__':
    main()
