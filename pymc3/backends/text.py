"""Text file trace backend

Store sampling values as CSV files.

File format
-----------

Sampling values for each chain are saved in a separate file (under a
directory specified by the `name` argument).  The rows correspond to
sampling iterations.  The column names consist of variable names and
index labels.  For example, the heading

  x,y__0_0,y__0_1,y__1_0,y__1_1,y__2_0,y__2_1

represents two variables, x and y, where x is a scalar and y has a
shape of (3, 2).
"""
from glob import glob
import numpy as np
import os
import pandas as pd
import warnings

from ..backends import base


class Text(base.BaseTrace):
    """Text trace object

    Parameters
    ----------
    name : str
        Name of directory to store text files
    model : Model
        If None, the model is taken from the `with` context.
    vars : list of variables
        Sampling values will be stored for these variables. If None,
        `model.unobserved_RVs` is used.
    """
    def __init__(self, name, model=None, vars=None):
        if not os.path.exists(name):
            os.mkdir(name)
        super(Text, self).__init__(name, model, vars)

        self.flat_names = {v: _create_flat_names(v, shape)
                           for v, shape in self.var_shapes.items()}

        self.filename = None
        self._fh = None
        self.df = None

    ## Sampling methods

    def setup(self, draws, chain):
        """Perform chain-specific setup.

        Parameters
        ----------
        draws : int
            Expected number of draws
        chain : int
            Chain number
        """
        self.chain = chain
        self.filename = os.path.join(self.name, 'chain-{}.csv'.format(chain))

        cnames = [fv for v in self.varnames for fv in self.flat_names[v]]

        if os.path.exists(self.filename):
            with open(self.filename) as fh:
                prev_cnames = next(fh).strip().split(',')
            if prev_cnames != cnames:
                raise base.BackendError(
                    "Previous file '{}' has different variables names "
                    "than current model.".format(self.filename))
            self._fh = open(self.filename, 'a')
        else:
            self._fh = open(self.filename, 'w')
            self._fh.write(','.join(cnames) + '\n')

    def record(self, point):
        """Record results of a sampling iteration.

        Parameters
        ----------
        point : dict
            Values mapped to variable names
        """
        vals = {}
        for varname, value in zip(self.varnames, self.fn(point)):
            vals[varname] = value.ravel()
        columns = [str(val) for var in self.varnames for val in vals[var]]
        self._fh.write(','.join(columns) + '\n')

    def close(self):
        self._fh.close()
        self._fh = None  # Avoid serialization issue.

    ## Selection methods

    def _load_df(self):
        if self.df is None:
            self.df = pd.read_csv(self.filename)

    def __len__(self):
        if self.filename is None:
            return 0
        self._load_df()
        return self.df.shape[0]

    def get_values(self, varname, burn=0, thin=1):
        """Get values from trace.

        Parameters
        ----------
        varname : str
        burn : int
        thin : int

        Returns
        -------
        A NumPy array
        """
        self._load_df()
        var_df = self.df[self.flat_names[varname]]
        shape = (self.df.shape[0],) + self.var_shapes[varname]
        vals = var_df.values.ravel().reshape(shape)
        return vals[burn::thin]

    def _slice(self, idx):
        warnings.warn('Slice for Text backend has no effect.')

    def point(self, idx):
        """Return dictionary of point values at `idx` for current chain
        with variables names as keys.
        """
        idx = int(idx)
        self._load_df()
        pt = {}
        for varname in self.varnames:
            vals = self.df[self.flat_names[varname]].iloc[idx]
            pt[varname] = vals.reshape(self.var_shapes[varname])
        return pt


def _create_flat_names(varname, shape):
    """Return flat variable names for `varname` of `shape`.

    Examples
    --------
    >>> _create_flat_names('x', (5,))
    ['x__0', 'x__1', 'x__2', 'x__3', 'x__4']

    >>> _create_flat_names('x', (2, 2))
    ['x__0_0', 'x__0_1', 'x__1_0', 'x__1_1']
    """
    if not shape:
        return [varname]
    labels = (np.ravel(xs).tolist() for xs in np.indices(shape))
    labels = (map(str, xs) for xs in labels)
    return ['{}__{}'.format(varname, '_'.join(idxs)) for idxs in zip(*labels)]


def _create_shape(flat_names):
    "Determine shape from `_create_flat_names` output."
    try:
        _, shape_str = flat_names[-1].rsplit('__', 1)
    except ValueError:
        return ()
    return tuple(int(i) + 1 for i in shape_str.split('_'))


def load(name, model=None):
    """Load Text database.

    Parameters
    ----------
    name : str
        Name of directory with files (one per chain)
    model : Model
        If None, the model is taken from the `with` context.

    Returns
    -------
    A MultiTrace instance
    """
    files = glob(os.path.join(name, 'chain-*.csv'))

    traces = []
    for f in files:
        chain = int(os.path.splitext(f)[0].rsplit('-', 1)[1])
        trace = Text(name, model=model)
        trace.chain = chain
        trace.filename = f
        traces.append(trace)
    return base.MultiTrace(traces)


def dump(name, trace, chains=None):
    """Store values from NDArray trace as CSV files.

    Parameters
    ----------
    name : str
        Name of directory to store CSV files in
    trace : MultiTrace of NDArray traces
        Result of MCMC run with default NDArray backend
    chains : list
        Chains to dump. If None, all chains are dumped.
    """
    if not os.path.exists(name):
        os.mkdir(name)
    if chains is None:
        chains = trace.chains

    var_shapes = trace._traces[chains[0]].var_shapes
    flat_names = {v: _create_flat_names(v, shape)
                  for v, shape in var_shapes.items()}

    for chain in chains:
        filename = os.path.join(name, 'chain-{}.csv'.format(chain))
        df = _trace_to_df(trace._traces[chain], flat_names)
        df.to_csv(filename, index=False)


def _trace_to_df(trace, flat_names=None):
    """Convert single-chain trace to Pandas DataFrame.

    Parameters
    ----------
    trace : NDarray trace
    flat_names : dict or None
        A dictionary that maps each variable name in `trace` to a list
        of flat variable names (e.g., ['x__0', 'x__1', ...])
    """
    if flat_names is None:
        flat_names = {v: _create_flat_names(v, shape)
                      for v, shape in trace.var_shapes.items()}

    var_dfs = []
    for varname, shape in trace.var_shapes.items():
        vals = trace.get_values(varname)
        if len(shape) == 1:
            flat_vals = vals
        else:
            flat_vals = vals.reshape(len(trace), np.prod(shape))
        var_dfs.append(pd.DataFrame(flat_vals, columns=flat_names[varname]))
    return pd.concat(var_dfs, axis=1)
