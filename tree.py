import numpy as np

import hgm_utils


def get_num_total_evals():
    return hgm_utils.nodes[0].get_sum(lambda node: node.num_evals)


class Node:
    def __init__(
        self,
        commit_id,
        utility_measures=None,
        parent_id=None,
        id=None,
    ):
        self.commit_id = commit_id
        self.children = []
        self.utility_measures = utility_measures if utility_measures else []
        self.parent_id = parent_id
        self.id = len(hgm_utils.nodes) if id is None else id
        hgm_utils.nodes[self.id] = self

    def get_sub_tree(self, fn=lambda self: self):
        if len(self.children) == 0:
            return [fn(self)]
        nodes_list = [fn(self)]
        for child in self.children:
            nodes_list.extend(child.get_sub_tree(fn))
        return nodes_list

    def get_pseudo_decendant_evals(self, num_pseudo):
        if self.num_evals < num_pseudo:
            return self.utility_measures
        return [self.mean_utility] * num_pseudo

    def get_decendant_evals(self, num_pseudo=10):
        decendant_evals = self.get_pseudo_decendant_evals(num_pseudo)
        for decendant in self.get_sub_tree()[1:]:
            decendant_evals += decendant.utility_measures
        return decendant_evals

    @property
    def num_evals(self):
        return len(self.utility_measures)

    @property
    def mean_utility(self):
        if self.num_evals == 0:
            return np.inf
        return np.sum(self.utility_measures) / self.num_evals

    def add_child(self, child):
        self.children.append(child)

    def save_as_dict(self):
        return {
            "commit_id": self.commit_id,
            "id": self.id,
            "parent_id": self.parent_id,
            "mean_utility": self.mean_utility,
            "num_evals": self.num_evals,
        }
