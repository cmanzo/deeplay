from typing import Callable, Dict, List, Tuple, Union, Iterable

from ..external import External
from .optimizer import Optimizer

import torch


class SGD(Optimizer):
    def __pre_init__(self, **optimzer_kwargs):
        super().__pre_init__(torch.optim.SGD, **optimzer_kwargs)

    def __init__(self, **optimzer_kwargs):
        super().__init__(**optimzer_kwargs)

    # def __init__(self, **optimzer_kwargs):
    #     super().__init__(**optimzer_kwargs)

    def params(
        self,
        func: Callable[
            [],
            Union[
                Iterable[torch.nn.Parameter],
                Dict[str, Iterable[torch.nn.Parameter]],
                List[Dict[str, Iterable[torch.nn.Parameter]]],
            ],
        ],
    ):
        self.configure(params=func)
        return self
