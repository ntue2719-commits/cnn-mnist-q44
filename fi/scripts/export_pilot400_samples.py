#!/usr/bin/env python3
"""Generate nine new MNIST bit-exact golden sample bundles using EXISTING P0 weights.

This script NEVER retrains a model or changes P0 weights/RTL. It refuses an
IMG0000 mismatch against the supplied frozen baseline. It requires the actual
six P0 weight/bias HEX files; no fabricated data or fake labels.
Dependencies on the user's Python environment: numpy, torchvision, torch.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
from pilot400_common import SAMPLES, ASSET_SIZES, asset_lines, sha256, validate_assets

PARAMS={'conv1_weight.hex': (36,(4,1,3,3)), 'conv1_bias.hex':(4,(4,)),
        'conv2_weight.hex': (288,(8,4,3,3)), 'conv2_bias.hex':(8,(8,)),
        'fc_weight.hex':(2000,(10,200)), 'fc_bias.hex':(10,(10,))}


def read_int8_hex(path:Path, count:int, shape):
    values=asset_lines(path,count,2)
    return np.array([v-256 if v>=128 else v for v in values],dtype=np.int8).reshape(shape)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--weights-dir',type=Path,required=True,help='Existing P0 data/ directory with all 6 weight+bias HEX files')
    ap.add_argument('--mnist-root',type=Path,required=True,help='Directory holding torchvision MNIST raw files')
    ap.add_argument('--download-mnist',action='store_true',help='Explicitly allow torchvision to download MNIST')
    args=ap.parse_args()
    repo=args.repo.resolve()
    weights_dir=args.weights_dir.resolve()
    out_root=repo/'fi/pilot400/samples'
    golden_file=repo/'python/golden_model.py'
    if not golden_file.is_file():
        raise ValueError(f'Independent bit-exact model not found: {golden_file}')
    sys.path.insert(0,str(golden_file.parent))
    from golden_model import (quantize_q44, conv2d_acc_q88, quant_relu_q44,
                              maxpool2x2_q44, fc_q44, argmax_q44,
                              write_int8_hex, write_int32_hex)
    from torchvision import datasets,transforms
    ps={k:read_int8_hex(weights_dir/k,*spec) for k,spec in PARAMS.items()}
    transform=transforms.Compose([transforms.ToTensor(),transforms.Normalize((0.1307,),(0.3081,))])
    ds=datasets.MNIST(root=str(args.mnist_root.resolve()),train=False,download=args.download_mnist,transform=transform)
    meta=[]
    for sid, dataset_idx in SAMPLES:
        img,label=ds[dataset_idx]
        input_q44=quantize_q44(img.numpy()[0]).reshape(1,28,28)
        c1=conv2d_acc_q88(input_q44,ps['conv1_weight.hex'],ps['conv1_bias.hex'])
        p1=maxpool2x2_q44(quant_relu_q44(c1))
        c2=conv2d_acc_q88(p1,ps['conv2_weight.hex'],ps['conv2_bias.hex'])
        p2=maxpool2x2_q44(quant_relu_q44(c2))
        fc=fc_q44(p2.flatten(),ps['fc_weight.hex'],ps['fc_bias.hex'])
        pred=argmax_q44(fc)
        sample_dir=out_root/sid
        sample_dir.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            t=Path(tmp)
            write_int8_hex(t/'input.hex',input_q44)
            write_int8_hex(t/'golden_pool1.hex',p1)
            write_int8_hex(t/'golden_pool2.hex',p2)
            write_int32_hex(t/'golden.hex',fc)
            write_int8_hex(t/'prediction.hex',np.array([pred],dtype=np.int8))
            for name,(count,digits) in ASSET_SIZES.items():
                generated=t/name
                asset_lines(generated,count,digits)
                existing=sample_dir/name
                if existing.exists() and generated.read_bytes()!=existing.read_bytes():
                    raise ValueError(f'MISMATCH with frozen or previous sample {sid}/{name}. '
                                     'Check original model weights, quantization and MNIST preprocessing; no overwrite performed.')
                if not existing.exists():
                    existing.write_bytes(generated.read_bytes())
        meta.append({'sample_id':sid,'mnist_test_index':dataset_idx,
                     'mnist_label':int(label),'golden_prediction':pred,
                     'input_sha256':sha256(sample_dir/'input.hex')})
        print(f'{sid} dataset_idx={dataset_idx} label={label} golden_pred={pred}',flush=True)
    validate_assets(out_root,require_all=True)
    (out_root/'sample_provenance.json').write_text(json.dumps({
        'transform':'torchvision ToTensor; Normalize(mean=0.1307,std=0.3081); existing Q4.4 quantize_q44',
        'weights_directory':str(weights_dir),'weight_hashes':{n:sha256(weights_dir/n) for n in PARAMS},
        'dataset':'torchvision.datasets.MNIST test split','samples':meta,
    },indent=2)+'\n',encoding='utf-8')
    print('PILOT400_SAMPLE_EXPORT_PASS: all 10 MNIST assets derived from original P0 weights')

if __name__=='__main__':
    try: main()
    except (ValueError,FileNotFoundError) as exc: raise SystemExit('PILOT400_EXPORT_FAIL: '+str(exc))
