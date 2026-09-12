"""Device selection and reproducibility in one place."""
import logging
import random

import numpy as np
import torch

logger = logging.getLogger(__name__)


def configure_cpu_threads(threads: int) -> None:
    torch.set_num_threads(threads)


def get_torch_device(requested: str = "auto") -> torch.device:
    available = {"cuda": torch.cuda.is_available(), "mps": torch.backends.mps.is_available(), "cpu": True}
    choice = next(name for name in ("cuda", "mps", "cpu") if available[name]) if requested == "auto" else requested
    if choice not in available or not available[choice]:
        raise ValueError(f"Requested device {choice} is unavailable")
    logger.info("PyTorch device=%s CUDA=%s MPS=%s", choice, available["cuda"], available["mps"])
    return torch.device(choice)


def set_random_seeds(seed: int, cpu_threads: int = 2) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    configure_cpu_threads(cpu_threads)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    # Unsupported deterministic accelerator kernels warn; platform identity is recorded.
    torch.use_deterministic_algorithms(True, warn_only=True)


def synchronize(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize()
    elif device.type == "mps":
        torch.mps.synchronize()
