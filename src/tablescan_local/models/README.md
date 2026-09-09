# Bundled local recognition models

No model download takes place when the application runs. Source documents and
cell images never leave the computer.

The two PP-OCRv5 models are from PaddleOCR, converted to ONNX by RapidAI
(Apache-2.0 upstream projects). Pinned distribution: RapidAI/RapidOCR v3.9.2.
Manifest: https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/default_models.yaml

- ch_PP-OCRv5_rec_server.onnx
  - https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_server.onnx
  - SHA-256: e09385400eaaaef34ceff54aeb7c4f0f1fe014c27fa8b9905d4709b65746562a
- en_PP-OCRv5_rec_mobile.onnx
  - https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx
  - SHA-256: c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8

Version 0.4 also includes the official PaddlePaddle PP-OCRv6 Medium recognition
model (Apache-2.0 model card):

- PP-OCRv6_medium_rec.onnx
  - https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx
  - pinned snapshot: 50c7eacafc52fa7bcf4194e8cd08e46f8558504b
  - SHA-256: 9c09abf0957f7968c7586464b7397b84ad2387a0497a351af40e9acc71b673ba
- ppocrv6_dict.txt (generated verbatim from that snapshot's `inference.yml`)
  - SHA-256: b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d

Version 0.5 adds a project-trained digit verifier:

- emnist_digit_cnn.onnx
  - training source: NIST EMNIST Digits, https://www.nist.gov/itl/products-and-services/emnist-dataset
  - held-out EMNIST Digits test accuracy: 99.585% (40,000 images)
  - SHA-256: 83a5e4159b528ce7c295a4309da57c8418410800643351bc3e75d3ce44dda073

Training and validation datasets are not distributed with the application or
this source repository.

The digit verifier is a secondary opinion over safely segmented candidates;
it is not allowed to override strong multi-model OCR evidence on its own.

These are research-grade recognizers, not a certified digit-reading system.
Model scores are not calibrated accuracy. Numeric values require human review.
