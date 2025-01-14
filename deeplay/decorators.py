from functools import wraps


def before_build(func):
    """Decorator for methods that will be run before build instead of immediately."""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        self.register_before_build_hook(
            lambda instance: func(instance, *args, **kwargs)
        )
        return self

    return wrapper


def after_build(func):
    """Decorator for methods that will be run after build instead of immediately.

    If the build method creates a new object, the hook will run on the new object.
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        self.register_after_build_hook(lambda instance: func(instance, *args, **kwargs))
        return self

    return wrapper
