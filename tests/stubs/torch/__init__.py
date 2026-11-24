class _Cuda:
    @staticmethod
    def is_available():
        return False

    @staticmethod
    def manual_seed_all(seed):
        pass

    @staticmethod
    def empty_cache():
        pass


cuda = _Cuda()


class Tensor:
    def __init__(self, shape=(1, 0)):
        self.shape = tuple(shape)


def zeros(shape, *args, **kwargs):
    return Tensor(shape)


def manual_seed(seed):
    pass


def concat(tensors, dim=0):
    return tensors[0]


cat = concat
