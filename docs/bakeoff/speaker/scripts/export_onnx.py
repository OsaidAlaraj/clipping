"""Export FabuLight-ASD (face-only weights) to ONNX so the app needs onnxruntime only: python export_onnx.py OUT.onnx (bake-off venv)."""
import sys, os, numpy as np, torch
from pathlib import Path
R = Path("L:/Projects/Clipping/.data/benchmark/repos/FabuLight-ASD"); os.chdir(R); sys.path.insert(0, str(R))
from ASD import ASD
s = ASD(bodyPose=False, upperBody=False); w = next((R / "exps").glob("*_F_fabulight_config/model/best_*.model")); s.loadParameters(str(w)); s.eval()
core = s.model.module; fc = s.lossFA.module.FC
class Pool5(torch.nn.Module):
    """The audio encoder feeds 4-D tensors to MaxPool3d (unbatched input); ONNX needs the batch axis made explicit."""
    def __init__(self, p): super().__init__(); self.p = p
    def forward(self, x): return self.p(x.unsqueeze(0)).squeeze(0)
for name in ("pool1", "pool2"):
    setattr(core.audioEncoder, name, Pool5(getattr(core.audioEncoder, name)))
class Net(torch.nn.Module):
    def forward(self, face, audio):
        o, _, _ = core(face, audio, None); return torch.softmax(fc(o), dim=-1)[:, 1]
net = Net().eval().cpu(); core.cpu(); fc.cpu()
for p in list(net.parameters()) + list(core.parameters()) + list(fc.parameters()): p.requires_grad_(False)
f = torch.rand(1, 50, 112, 112) * 255; a = torch.randn(1, 200, 13)
torch.onnx.export(net, (f, a), sys.argv[1], input_names=["face", "audio"], output_names=["p"], dynamic_axes={"face": {1: "T"}, "audio": {1: "A"}, "p": {0: "T"}}, opset_version=17, dynamo=False)
print("exported", Path(sys.argv[1]).stat().st_size / 1e6, "MB")
import onnx
m = onnx.load(sys.argv[1]); n = 0
for node in m.graph.node:
    if node.op_type == "MaxPool":
        for a in list(node.attribute):
            if a.name == "dilations" and all(d == 1 for d in a.ints): node.attribute.remove(a); n += 1
onnx.save(m, sys.argv[1]); print("stripped dilations on", n, "MaxPool nodes")
