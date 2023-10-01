from __future__ import annotations
import re
import warnings

from . import (
    ClassSelector,
    WildcardSelector,
    DoubleWildcardSelector,
    IndexSelector,
    NoneSelector,
    Ref,
    parse_selectors,
)
from ..utils import (
    safe_call,
    match_signature as _match_signature,
)

import inspect
import torch

__all__ = ["Config", "ForwardHook"]

default_obj = object()


class ConfigRule:
    """
    Represents a rule in the configuration.

    Attributes:
        selector (Selector): The selector of the rule.
        head (Selector): The parsed selector key.
        key (str): The key of the rule.
        value (Any): The value of the rule.
        scope_root (Selector): The root context of the rule.

    Methods:
        is_more_specific_than(other): Check if the rule is more specific than another rule based on their specificity values.
        matches(context, match_key=False, allow_indexed=False): Check if the rule matches the given context.
        get_value(config): Get the value of the rule from the config.
    """

    specificity = 1
    default = False

    def __init__(self, selector, key, value):
        """
        Initialize a ConfigRule instance.

        Args:
            selector (Selector): The selector of the rule.
            key (str): The key of the rule.
            value (Any): The value of the rule.
        """
        self.selector = selector

        head = parse_selectors(key)

        self.head = head
        self.key = head.key()
        self.value = value
        self.scope_root = NoneSelector()

        # self._selector_has_wildcard = selector.any(lambda s: isinstance(s, WildcardSelector))
        # self._selector_has_double_wildcard = selector.any(lambda s: isinstance(s, DoubleWildcardSelector))

    def is_more_specific_than(self, other):
        """
        Check if the rule is more specific than another rule based on their specificity values.

        Args:
            other (ConfigRule): The other rule to compare.

        Returns:
            bool: True if the rule is more specific than the other rule, False otherwise.
        """
        if other is None:
            return True

        if self.specificity != other.specificity:
            return self.specificity > other.specificity

        # Otherwise, last added selector is more specific
        # In the future, we should consider wildcard selectors as less specific
        return True

    def matches(self, context, match_key=False, allow_indexed=False):
        """
        Check if the rule matches the given context.

        Args:
            context (Selector): The context to match against.
            match_key (bool): Whether to match the key of the rule.
            allow_indexed (bool): Whether to allow indexed selectors.

        Returns:
            bool: True if the rule matches the context, False otherwise.
        """
        head = ClassSelector(self.key) if allow_indexed else self.head
        if match_key:
            full_selector = self.selector + head
        else:
            full_selector = self.selector

        # Handle the case where either or both the full selector and the context are None
        full_selector_is_none = isinstance(full_selector, NoneSelector)
        context_is_none = isinstance(context, NoneSelector)
        if full_selector_is_none and context_is_none:
            return True
        if full_selector_is_none or context_is_none:
            return False

        regex = full_selector.regex()

        for selector in context:
            if re.match(regex, selector):
                return True

        return False

    def get_value(self, config):
        """
        Get the value of the rule from the config.

        Args:
            config (Config): The config to retrieve the value from.

        Returns:
            Any: The value of the rule.
        """
        if isinstance(self.value, Ref):
            # Create a new config with the root context of the rule
            new_config = Config(config._rules, config._refs, self.scope_root)

            # Get the value of the ref, should be a unique selector
            referenced_value = new_config.get(
                self.value.selectors, return_dict_if_multiple=False
            )

            # Evaluate the ref function
            return self.value(referenced_value)

        if isinstance(self.value, ForwardHook):
            return self.value.value()

        return self.value

    def __repr__(self):
        return (
            str(self.selector)
            + "."
            + str(self.head)
            + " = "
            + str(self.value)
            + (" (default)" if self.default else "")
        )


class ConfigRuleDefault(ConfigRule):
    """
    Represents a default rule in the configuration.

    Attributes:
    - specificity: An integer representing the specificity of the rule. Default value is 0.
    - default: A boolean indicating whether the rule is a default rule. Default value is True.
    """

    specificity = 0
    default = True


class ConfigRuleWrapper(ConfigRule):
    """
    A subclass of ConfigRule that wraps another ConfigRule instance.

    This class allows for additional functionality to be added to the wrapped rule without modifying the original class.
    Specifically useful when merging Configs together.

    Example Usage:
    ```python
    rule = ConfigRule(ClassSelector('selector'), 'key', 'value')
    wrapper = ConfigRuleWrapper(ClassSelector('new_selector'), rule)
    print(wrapper.selector)  # Output: 'new_selector.selector'
    print(wrapper.key)  # Output: 'key'
    print(wrapper.value)  # Output: 'value'
    ```
    """

    def __init__(self, selector, value, default=False):
        """
        Initializes a ConfigRuleWrapper instance.

        Args:
            selector (str): The selector of the wrapped rule combined with the selector of the wrapper.
            value (ConfigRule): The wrapped ConfigRule instance.
            default (bool, optional): If True, sets the specificity to 0 and the default flag to True. Defaults to False.
        """
        if default:
            self.specificity = 0
            self.default = True

        self.selector = selector + value.selector
        self.scope_root = selector + value.scope_root
        self.key = value.key
        self.head = value.head
        self.value = value.value

    def __getattr__(self, name):
        """
        Delegates attribute access to the wrapped rule if the attribute is not found in the wrapper.

        Args:
            name (str): The name of the attribute to access.

        Returns:
            Any: The value of the attribute from the wrapped rule.
        """
        return getattr(self.value, name)


class ForwardHook:
    """
    A class used to create a hook that can be called on a target object.

    Will run on the first forward pass of the direct parent module. Can be used to
    retrieve information from the forward pass of a module.

    Attributes:
    - hook: The hook function to be executed on the target object.
    - first_only: A boolean indicating whether the hook should run only once.

    Methods:
    - __call__(self, x): Calls the hook function on a target object and stores the result.
    - value(self): Returns the stored result of the hook function.
    - has_run(self): Returns a boolean indicating whether the hook has been run.

    Example Usage:
    ```python
    model = Layer("layer").from_config(
        Config()
        .layer(nn.Linear)
        .layer.in_features(ForwardHook(lambda x: x.shape[1]))
        .layer.out_features(10)
    )

    y = model(torch.ones(1, 5))
    print(model.in_features) # Output: 5
    """

    def __init__(self, hook, first_only=True):
        """
        Initializes a ForwardHook instance with a hook function and an option to run only once.

        Parameters:
        - hook: The hook function to be executed on the target object.
        - first_only: A boolean indicating whether the hook should run only once.
        """
        if isinstance(hook, ForwardHook):
            hook = hook.hook
            first_only = hook.first_only

        self.hook = hook
        self.first_only = first_only

        self._value = None
        self._has_run = False

    def __call__(self, module, *args, **kwargs):
        """
        Calls the hook function on a target object and stores the result.

        Parameters:
        - x: The target object to call the hook function on.

        Returns:
        - The result of the hook function.
        """
        if self.first_only and self._has_run:
            return self._value
        self._value = self.hook(module, *args, **kwargs)
        self._has_run = True

    def value(self):
        """
        Returns the stored result of the hook function.

        Returns:
        - The stored result of the hook function.

        Raises:
        - ValueError: If the hook has not been run yet.
        """
        if not self._has_run:
            raise ValueError(
                "Hook has not been run yet. Make sure the module is evaluated before the target is called."
            )
        return self._value

    def has_run(self):
        """
        Returns a boolean indicating whether the hook has been run.

        Returns:
        - True if the hook has been run, False otherwise.
        """
        return self._has_run


class Config:
    """
    The `Config` class is a configuration management class that allows users to define and manipulate configuration rules. It provides methods for setting and getting values, merging configurations, adding references, and more.

    Example Usage:
    >>> # Create a new Config object
    >>> config = Config()
    >>>
    >>> # Set values using selectors
    >>> config.a.b.c(10).d.e.f(20)

    >>> # Get a value using selectors
    >>> c = config.get("a.b.c")  # returns 10
    >>> f = config.get("d.e.f")  # returns 20

    >>> # Merge two configurations
    >>> config1 = Config().a(1)
    >>> config2 = Config().b(2)
    >>> merged_config = config1.merge("other", config2)  # merges config1 and config2
    >>> a = merged_config.get("a")  # returns 1
    >>> b = merged_config.get("other.b")  # returns 2

    >>> # Add a reference to another rule
    >>> ref_config = Config().x.y(100).a.b(Ref("x.y", lambda x: x + 1))
    >>> a = ref_config.get("a.b")  # returns 101


    Main functionalities:
    - Setting and getting values using selectors
    - Merging configurations
    - Adding and retrieving references to other configurations
    - Populating configurations with values from generators
    - Running forward hooks

    Methods:
    - on_first_forward(): Sets a hook to be evaluated only on the first forward pass.
    - on_forward(): Sets a hook to be evaluated on every forward pass.
    - populate(): Populates the configuration with values from a generator.
    - default(): Sets a default value for the given selectors.
    - merge(): Merges another configuration into the current configuration.
    - get(): Retrieves a value for the given selectors.
    - get_parameters(): Retrieves the parameters of the configuration.
    - with_selector(): Creates a new configuration with additional selectors.

    """

    def __init__(self, rules=None, refs=None, context=NoneSelector()):
        self._rules = [] if rules is None else rules.copy()
        self._refs = {} if refs is None else refs
        self._context = context

    def on_first_forward(self, target, hook):
        return self.on_forward(target, hook, first_only=True)

    def on_forward(self, target, hook, first_only=False):
        if not first_only:
            raise NotImplementedError("Only first_only is supported for now")

        target = parse_selectors(target)

        body, head = target.pop()
        _key = ClassSelector("___" + str(head))

        # Create a Ref from target to the current context + _key.
        # On forward, context + _key will be evaluated.
        # The remote rule will automatically reflect the changes.
        self._rules.append(ConfigRule(body, head, Ref(self._context + _key)))

        # Create a rule that will be evaluated on forward
        self._rules.append(
            ConfigRule(self._context, _key, ForwardHook(hook, first_only=first_only))
        )

        return Config(self._rules, self._refs)

    def run_all_forward_hooks(self, x):
        for rule in self.get_all_forward_hooks():
            rule.value(x)

    def has_forward_hooks(self):
        return len(self.get_all_forward_hooks()) > 0

    def get_all_forward_hooks(self):
        rules = self._get_all_matching_rules(NoneSelector(), match_key=False)
        module = self._get_all_matching_rules(NoneSelector(), match_key=True)

        all_rules = rules + module
        return [rule for rule in all_rules if isinstance(rule.value, ForwardHook)]

    def set(self, selectors, value, default=False):
        selectors = parse_selectors(selectors)

        if isinstance(selectors, NoneSelector):
            if isinstance(self._context, NoneSelector):
                raise ValueError("Cannot set a value with no context and no selector.")
            selectors, key = self._context.pop()
        else:
            selectors, key = selectors.pop()
            selectors = self._context + selectors

        if default:
            self._rules.append(ConfigRuleDefault(selectors, key, value))
        else:
            self._rules.append(ConfigRule(selectors, key, value))
        return self

    def populate(self, selectors, generator, length=None):
        # idx is interpreted as follows:
        # None: every index.
        # int: all indices up to but not including idx

        selectors = parse_selectors(selectors)
        selectors, key = selectors.pop()

        body, head = self._context.pop()

        if isinstance(head, IndexSelector):
            new_head = IndexSelector(head.selector, head.index, length)
            integers_to_populate = new_head.get_list_of_indices()

            if callable(generator):
                generator = [generator(i) for i in integers_to_populate]

            for i, value in zip(integers_to_populate, generator):
                self._rules.append(
                    ConfigRule(
                        (body + new_head.selector)[i] + selectors, str(key), value
                    )
                )

        else:
            if length is None and not hasattr(generator, "__len__"):
                # Warn that we will only populate up to index 256
                # Only warn once
                warnings.warn(
                    """Populating a config with a generator of unknown length without specifying the length will only populate up to index 256.
To populate more, specify the length with .populate(..., length=desired_length)"""
                )

            length = length or 256

            if callable(generator):
                generator = [generator(i) for i in range(length)]

            # Is there any case where this is not equivalent to enumerate?
            for i, value in zip(range(length), generator):
                self._rules.append(
                    ConfigRule(self._context[i] + selectors, str(key), value)
                )

        return Config(self._rules, self._refs)

    def default(self, selectors, value):
        return self.set(selectors, value, default=True)

    def merge(self, selectors=None, config=None, as_default=False, prepend=False):
        if config is None:
            config = selectors
            selectors = None

        selectors = parse_selectors(selectors)

        additional_rules = []
        for rule in config._rules:
            wrapped_rule = ConfigRuleWrapper(
                self._context + selectors, rule, default=as_default
            )
            additional_rules.append(wrapped_rule)

        if prepend:
            self._rules = additional_rules + self._rules
        else:
            self._rules = self._rules + additional_rules

        return Config(self._rules, self._refs)

    def get(self, selectors, default=default_obj, return_dict_if_multiple=False):
        selectors = parse_selectors(selectors)
        full_context = self._context + selectors

        # check if the last selector is a index selector
        last_selector_is_index = isinstance(full_context.pop()[-1], IndexSelector)

        rules = self._get_all_matching_rules(
            selectors, match_key=True, allow_indexed=not last_selector_is_index
        )
        rules_per_key = self._merge_rules_on_key(rules)

        if not last_selector_is_index:
            most_specific = self._take_most_specific_per_key_and_index(rules_per_key)
        else:
            most_specific = self._take_most_specific_per_key(rules_per_key)

        if len(most_specific) == 0:
            if default is not default_obj:
                return default
            else:
                raise ValueError(f"No keys match for {str(full_context)} in {self}")
        if len(most_specific) == 1:
            return list(most_specific.values())[0]
        if return_dict_if_multiple:
            return most_specific

        raise ValueError(
            f"Multiple keys match {selectors} ({list(most_specific.keys())})"
        )

    def get_module(self):
        return self.get(NoneSelector())

    def add_ref(self, name, config):
        if name in self._refs:
            warnings.warn(
                f"UID {name} already exists with value {self._refs[name]}. It will be overwritten."
            )
        self._refs[name] = config

    def get_ref(self, name):
        return self._refs[name]

    def get_parameters(self, create=True):
        rules = self._get_all_matching_rules(NoneSelector(), match_key=False)
        rule_dict = self._merge_rules_on_key(rules)
        rule_dict = self._take_most_specific_per_key(rule_dict)

        if create:
            rule_dict = self._initialize_classes(rule_dict)

        return rule_dict

    def with_selector(self, selectors):
        selectors = parse_selectors(selectors)
        return Config(self._rules, self._refs, self._context + selectors)

    def __getattr__(self, name) -> Config:
        match name:
            case "_":
                selector = WildcardSelector()
            case "__":
                selector = DoubleWildcardSelector()
            case _:
                selector = ClassSelector(name)
        return Config(self._rules, self._refs, self._context + selector)

    def __getitem__(self, index):
        if isinstance(self._context, NoneSelector):
            raise ValueError(
                "Cannot index a config with no context. Use a class selector first"
            )

        if isinstance(index, tuple):
            index, length = index
        else:
            length = None
        return Config(self._rules, self._refs, self._context[index, length])

    def __call__(self, *x, **kwargs):
        if len(x) > 2:
            raise ValueError(
                "Config can only be called with one or two positional arguments"
            )

        new_config = Config(self._rules.copy(), self._refs, self._context)

        if len(x) == 1:
            x = x[0]
            new_config.set(None, x)

        elif len(x) == 2:
            x, subconfig = x
            new_config.set(None, x)
            new_config.merge(subconfig)

        for key, value in kwargs.items():
            new_config.set(key, value)

        # reset context
        new_config._context = NoneSelector()

        return new_config

    def __repr__(self):
        return "Config(\n" + "\n".join([str(rule) for rule in self._rules]) + "\n)"

    def _get_all_matching_rules(self, selectors, match_key=True, allow_indexed=False):
        contextualized_selectors = self._context + selectors
        return [
            rule
            for rule in self._rules
            if rule.matches(
                contextualized_selectors,
                match_key=match_key,
                allow_indexed=allow_indexed,
            )
        ]

    def _is_last_selector_a(self, type):
        if isinstance(self._context, NoneSelector):
            return type == NoneSelector
        _, head = self._context.pop()
        return isinstance(head, type)

    def _take_most_specific_per_key(self, key_rule_dict):
        most_specific = {}
        for key, rules in key_rule_dict.items():
            try:
                most_specific[key] = Config._take_most_specific_in_list(
                    rules
                ).get_value(self)
            except (ValueError, TypeError):
                # In case of forward hooks that have not been run yet, we may get a ValueError
                # In this case we assume that the rule is not necessary to build the module
                pass
        return most_specific

    @staticmethod
    def _take_most_specific_in_list(rules):
        most_specific = rules[0]
        for rule in rules:
            if rule.is_more_specific_than(most_specific):
                most_specific = rule
        return most_specific

    def _take_most_specific_per_key_and_index(self, key_rule_dict):
        # This function is awful. It needs to be rewritten.

        most_specific = {}

        for key, rules in key_rule_dict.items():
            rule_if_no_indexed = []
            any_indexed = False

            indexed_values = {}
            for rule in rules:
                if isinstance(rule.head, IndexSelector):
                    any_indexed = True
                    for index in rule.head.get_list_of_indices():
                        if index not in indexed_values:
                            indexed_values[index] = [rule]
                        else:
                            indexed_values[index].append(rule)
                else:
                    rule_if_no_indexed.append(rule)

            if len(rule_if_no_indexed) == 0:
                least_specific_rule = ConfigRule(NoneSelector(), "", [])
                least_specific_rule.specificity = -9999
                rule_if_no_indexed = [least_specific_rule]

            most_specific_rule_if_no_index = Config._take_most_specific_in_list(
                rule_if_no_indexed
            )
            value_if_no_indexed = most_specific_rule_if_no_index.get_value(self)

            if not any_indexed:
                most_specific[key] = value_if_no_indexed
                continue

            # We will use this to fill potential missing indices
            if not isinstance(value_if_no_indexed, (list, tuple)):
                value_if_no_indexed = [value_if_no_indexed]

            for idx, value in enumerate(value_if_no_indexed):
                if idx not in indexed_values or (
                    most_specific_rule_if_no_index.specificity
                    > max(r.specificity for r in indexed_values[idx])
                ):
                    indexed_values[idx] = [most_specific_rule_if_no_index]

            most_specific_for_key = {}
            for index, rules in indexed_values.items():
                most_specific_for_key[index] = Config._take_most_specific_in_list(rules)

            indices = list(indexed_values.keys())
            indices.sort()
            missing_indices = [i for i in range(indices[-1]) if i not in indices]
            assert (
                len(missing_indices) == 0
            ), f"Missing indices {missing_indices} for key {key}"

            most_specific_values = [
                most_specific_for_key[i].get_value(self) for i in indices
            ]
            for i in indices:
                if not isinstance(most_specific_for_key[i].head, IndexSelector):
                    most_specific_values[i] = most_specific_values[i][i]
            most_specific[key] = most_specific_values

        return most_specific

    def _initialize_classes(self, rule_dict):
        initialized = {}
        for key, value in rule_dict.items():
            initialized[key] = self.with_selector(key).build_object(value)
        return initialized

    @staticmethod
    def _merge_rules_on_key(rules):
        merged = {}
        for rule in rules:
            key = rule.key
            if key in merged:
                merged[key].append(rule)
            else:
                merged[key] = [rule]
        return merged

    def build_object(self, template):
        from ..templates import Layer

        if isinstance(template, (list, tuple)):
            return [self[i].build_object(template[i]) for i in range(len(template))]
        elif isinstance(template, Layer):
            return template.from_config(self)
        elif inspect.isclass(template) and hasattr(template, "from_config"):
            return template.from_config(self)
        elif isinstance(template, torch.nn.Module):
            return template
        elif callable(template):
            subparams = self.get_parameters(create=False)
            if inspect.isclass(template):
                _factory_kwargs = _match_signature(template.__init__, [], subparams)
            else:
                _factory_kwargs = _match_signature(template, [], subparams)
            # recurse.

            _factory_kwargs = self._initialize_classes(_factory_kwargs)
            return template(**_factory_kwargs)
        else:
            return template
