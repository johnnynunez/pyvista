"""Container to mimic ``vtkMultiBlockDataSet`` objects.

These classes hold many VTK datasets in one object that can be passed
to VTK algorithms and PyVista filtering/plotting routines.
"""

from __future__ import annotations

from collections.abc import MutableSequence
from itertools import zip_longest
import pathlib
from typing import TYPE_CHECKING
from typing import Any
from typing import Union
from typing import cast
from typing import overload

import numpy as np

import pyvista
from pyvista.core import _validation

from . import _vtk_core as _vtk
from ._typing_core import BoundsTuple
from .dataobject import DataObject
from .dataset import DataSet
from .filters import CompositeFilters
from .pyvista_ndarray import pyvista_ndarray
from .utilities.arrays import CellLiteral
from .utilities.arrays import FieldAssociation
from .utilities.arrays import PointLiteral
from .utilities.arrays import parse_field_choice
from .utilities.geometric_objects import Box
from .utilities.helpers import is_pyvista_dataset
from .utilities.helpers import wrap

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ._typing_core import NumpyArray

_TypeMultiBlockLeaf = Union['MultiBlock', DataSet, None]


class MultiBlock(
    CompositeFilters,
    DataObject,
    MutableSequence,  # type: ignore[type-arg]
    _vtk.vtkMultiBlockDataSet,
):
    """A composite class to hold many data sets which can be iterated over.

    This wraps/extends the `vtkMultiBlockDataSet
    <https://vtk.org/doc/nightly/html/classvtkMultiBlockDataSet.html>`_ class
    so that we can easily plot these data sets and use the composite in a
    Pythonic manner.

    You can think of ``MultiBlock`` like a list as we
    can iterate over this data structure by index.  It has some dictionary
    features as we can also access blocks by their string name.

    .. versionchanged:: 0.36.0
       ``MultiBlock`` adheres more closely to being list like, and inherits
       from :class:`collections.abc.MutableSequence`.  Multiple nonconforming
       behaviors were removed or modified.

    Parameters
    ----------
    *args : dict, optional
        Data object dictionary.

    **kwargs : dict, optional
        See :func:`pyvista.read` for additional options.

    Examples
    --------
    >>> import pyvista as pv

    Create an empty composite dataset.

    >>> blocks = pv.MultiBlock()

    Add a dataset to the collection.

    >>> sphere = pv.Sphere()
    >>> blocks.append(sphere)

    Add a named block.

    >>> blocks['cube'] = pv.Cube()

    Instantiate from a list of objects.

    >>> data = [
    ...     pv.Sphere(center=(2, 0, 0)),
    ...     pv.Cube(center=(0, 2, 0)),
    ...     pv.Cone(),
    ... ]
    >>> blocks = pv.MultiBlock(data)
    >>> blocks.plot()

    Instantiate from a dictionary.

    >>> data = {
    ...     'cube': pv.Cube(),
    ...     'sphere': pv.Sphere(center=(2, 2, 0)),
    ... }
    >>> blocks = pv.MultiBlock(data)
    >>> blocks.plot()

    Iterate over the collection.

    >>> for name in blocks.keys():
    ...     block = blocks[name]

    >>> for block in blocks:
    ...     # Do something with each dataset
    ...     surf = block.extract_surface()

    """

    plot = pyvista._plot.plot

    _WRITERS = dict.fromkeys(['.vtm', '.vtmb'], _vtk.vtkXMLMultiBlockDataWriter)

    def __init__(self: MultiBlock, *args, **kwargs) -> None:
        """Initialize multi block."""
        super().__init__()
        deep = kwargs.pop('deep', False)

        # keep a python reference to the dataset to avoid
        # unintentional garbage collections since python does not
        # add a reference to the dataset when it's added here in
        # MultiBlock.  See https://github.com/pyvista/pyvista/pull/1805
        self._refs: Any = {}

        if len(args) == 1:
            if isinstance(args[0], _vtk.vtkMultiBlockDataSet):
                if deep:
                    self.deep_copy(args[0])
                else:
                    self.shallow_copy(args[0])
            elif isinstance(args[0], (list, tuple)):
                for block in args[0]:
                    self.append(block)
            elif isinstance(args[0], (str, pathlib.Path)):
                self._from_file(args[0], **kwargs)
            elif isinstance(args[0], dict):
                for key, block in args[0].items():
                    self.append(block, key)
            else:
                raise TypeError(f'Type {type(args[0])} is not supported by pyvista.MultiBlock')

        elif len(args) > 1:
            raise ValueError(
                'Invalid number of arguments:\n``pyvista.MultiBlock``supports 0 or 1 arguments.',
            )

        # Upon creation make sure all nested structures are wrapped
        self.wrap_nested()

    def wrap_nested(self: MultiBlock) -> None:
        """Ensure that all nested data structures are wrapped as PyVista datasets.

        This is performed in place.

        """
        for i in range(self.n_blocks):
            block = self.GetBlock(i)
            if not is_pyvista_dataset(block):
                self.SetBlock(i, wrap(block))

    @property
    def bounds(self: MultiBlock) -> BoundsTuple:
        """Find min/max for bounds across blocks.

        Returns
        -------
        tuple[float, float, float, float, float, float]
            Length 6 tuple of floats containing min/max along each axis.

        Examples
        --------
        Return the bounds across blocks.

        >>> import pyvista as pv
        >>> data = [
        ...     pv.Sphere(center=(2, 0, 0)),
        ...     pv.Cube(center=(0, 2, 0)),
        ...     pv.Cone(),
        ... ]
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.bounds
        BoundsTuple(x_min=-0.5, x_max=2.5, y_min=-0.5, y_max=2.5, z_min=-0.5, z_max=0.5)

        """
        # apply reduction of min and max over each block
        # (typing.cast necessary to make mypy happy with ufunc.reduce() later)
        all_bounds = [cast(list[float], block.bounds) for block in self if block]
        # edge case where block has no bounds
        if not all_bounds:  # pragma: no cover
            minima = (0.0, 0.0, 0.0)
            maxima = (0.0, 0.0, 0.0)
        else:
            minima = np.minimum.reduce(all_bounds)[::2].tolist()
            maxima = np.maximum.reduce(all_bounds)[1::2].tolist()

        # interleave minima and maxima for bounds
        return BoundsTuple(minima[0], maxima[0], minima[1], maxima[1], minima[2], maxima[2])

    @property
    def center(self: MultiBlock) -> tuple[float, float, float]:
        """Return the center of the bounding box.

        Returns
        -------
        tuple[float, float, float]
            Center of the bounding box.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = [
        ...     pv.Sphere(center=(2, 0, 0)),
        ...     pv.Cube(center=(0, 2, 0)),
        ...     pv.Cone(),
        ... ]
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.center  # doctest:+SKIP
        array([1., 1., 0.])

        """
        return tuple(np.reshape(self.bounds, (3, 2)).mean(axis=1).tolist())

    @property
    def length(self: MultiBlock) -> float:
        """Return the length of the diagonal of the bounding box.

        Returns
        -------
        float
            Length of the diagonal of the bounding box.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = [
        ...     pv.Sphere(center=(2, 0, 0)),
        ...     pv.Cube(center=(0, 2, 0)),
        ...     pv.Cone(),
        ... ]
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.length
        4.3584

        """
        return Box(self.bounds).length

    @property
    def n_blocks(self: MultiBlock) -> int:
        """Return the total number of blocks set.

        Returns
        -------
        int
            Total number of blocks set.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = [
        ...     pv.Sphere(center=(2, 0, 0)),
        ...     pv.Cube(center=(0, 2, 0)),
        ...     pv.Cone(),
        ... ]
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.n_blocks
        3

        """
        return self.GetNumberOfBlocks()

    @n_blocks.setter
    def n_blocks(self: MultiBlock, n: int) -> None:
        """Change the total number of blocks set.

        Parameters
        ----------
        n : int
            The total number of blocks set.

        """
        self.SetNumberOfBlocks(n)
        self.Modified()

    @property
    def volume(self: MultiBlock) -> float:
        """Return the total volume of all meshes in this dataset.

        Returns
        -------
        float
            Total volume of the mesh.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = [
        ...     pv.Sphere(center=(2, 0, 0)),
        ...     pv.Cube(center=(0, 2, 0)),
        ...     pv.Cone(),
        ... ]
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.volume
        1.7348

        """
        return sum(block.volume for block in self if block)

    def get_data_range(  # type: ignore[override]
        self: MultiBlock, name: str, allow_missing: bool = False
    ) -> tuple[float, float]:
        """Get the min/max of an array given its name across all blocks.

        Parameters
        ----------
        name : str
            Name of the array.

        allow_missing : bool, default: False
            Allow a block to be missing the named array.

        Returns
        -------
        tuple
            ``(min, max)`` of the named array.

        """
        mini, maxi = np.inf, -np.inf
        for i in range(self.n_blocks):
            data = self[i]
            if data is None:
                continue
            # get the scalars if available - recursive
            try:
                tmi, tma = data.get_data_range(name)
            except KeyError:
                if allow_missing:
                    continue
                else:
                    raise
            if not np.isnan(tmi) and tmi < mini:
                mini = tmi
            if not np.isnan(tma) and tma > maxi:
                maxi = tma
        return mini, maxi

    def get_index_by_name(self: MultiBlock, name: str) -> int:
        """Find the index number by block name.

        Parameters
        ----------
        name : str
            Name of the block.

        Returns
        -------
        int
            Index of the block.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.get_index_by_name('sphere')
        1

        """
        for i in range(self.n_blocks):
            if self.get_block_name(i) == name:
                return i
        raise KeyError(f'Block name ({name}) not found')

    @overload
    def __getitem__(
        self: MultiBlock,
        index: int | str,
    ) -> _TypeMultiBlockLeaf: ...  # pragma: no cover

    @overload
    def __getitem__(self: MultiBlock, index: slice) -> MultiBlock: ...  # pragma: no cover

    def __getitem__(self: MultiBlock, index):
        """Get a block by its index or name.

        If the name is non-unique then returns the first occurrence.

        """
        if isinstance(index, slice):
            multi = MultiBlock()
            for i in range(self.n_blocks)[index]:
                multi.append(self[i], self.get_block_name(i))
            return multi
        elif isinstance(index, str):
            index = self.get_index_by_name(index)
        ############################
        if index < -self.n_blocks or index >= self.n_blocks:
            raise IndexError(f'index ({index}) out of range for this dataset.')
        if index < 0:
            index = self.n_blocks + index

        return wrap(self.GetBlock(index))

    def append(self: MultiBlock, dataset: _TypeMultiBlockLeaf, name: str | None = None) -> None:
        """Add a data set to the next block index.

        Parameters
        ----------
        dataset : pyvista.DataSet or pyvista.MultiBlock
            Dataset to append to this multi-block.

        name : str, optional
            Block name to give to dataset.  A default name is given
            depending on the block index as ``'Block-{i:02}'``.

        Examples
        --------
        >>> import pyvista as pv
        >>> from pyvista import examples
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.append(pv.Cone())
        >>> len(blocks)
        3
        >>> blocks.append(examples.load_uniform(), 'uniform')
        >>> blocks.keys()
        ['cube', 'sphere', 'Block-02', 'uniform']

        """
        # do not allow to add self
        if dataset is self:
            raise ValueError('Cannot nest a composite dataset in itself.')

        index = self.n_blocks  # note off by one so use as index
        # always wrap since we may need to reference the VTK memory address
        wrapped = wrap(dataset)
        if isinstance(wrapped, pyvista_ndarray):
            raise TypeError('dataset should not be or contain an array')
        dataset = wrapped
        self.n_blocks += 1
        self[index] = dataset
        # No overwrite if name is None
        self.set_block_name(index, name)

    def extend(self: MultiBlock, datasets: Iterable[_TypeMultiBlockLeaf]) -> None:
        """Extend MultiBlock with an Iterable.

        If another MultiBlock object is supplied, the key names will
        be preserved.

        Parameters
        ----------
        datasets : Iterable[pyvista.DataSet or pyvista.MultiBlock]
            Datasets to extend.

        Examples
        --------
        >>> import pyvista as pv
        >>> from pyvista import examples
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks_uniform = pv.MultiBlock({'uniform': examples.load_uniform()})
        >>> blocks.extend(blocks_uniform)
        >>> len(blocks)
        3
        >>> blocks.keys()
        ['cube', 'sphere', 'uniform']

        """
        # Code based on collections.abc
        if isinstance(datasets, MultiBlock):
            for key, data in zip(datasets.keys(), datasets):
                self.append(data, key)
        else:
            for v in datasets:
                self.append(v)

    def get(
        self: MultiBlock,
        index: str,
        default: _TypeMultiBlockLeaf = None,
    ) -> _TypeMultiBlockLeaf:
        """Get a block by its name.

        If the name is non-unique then returns the first occurrence.
        Returns ``default`` if name isn't in the dataset.

        Parameters
        ----------
        index : str
            Index or name of the dataset within the multiblock.

        default : pyvista.DataSet or pyvista.MultiBlock, optional
            Default to return if index is not in the multiblock.

        Returns
        -------
        pyvista.DataSet or pyvista.MultiBlock or None
            Dataset from the given index if it exists.

        Examples
        --------
        >>> import pyvista as pv
        >>> from pyvista import examples
        >>> data = {'poly': pv.PolyData(), 'img': pv.ImageData()}
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.get('poly')
        PolyData ...
        >>> blocks.get('cone')

        """
        try:
            return self[index]
        except KeyError:
            return default

    def set_block_name(self: MultiBlock, index: int, name: str | None) -> None:
        """Set a block's string name at the specified index.

        Parameters
        ----------
        index : int
            Index or the dataset within the multiblock.

        name : str, optional
            Name to assign to the block at ``index``. If ``None``, no name is
            assigned to the block.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.append(pv.Cone())
        >>> blocks.set_block_name(2, 'cone')
        >>> blocks.keys()
        ['cube', 'sphere', 'cone']

        """
        if name is None:
            return
        index = range(self.n_blocks)[index]
        self.GetMetaData(index).Set(_vtk.vtkCompositeDataSet.NAME(), name)
        self.Modified()

    def get_block_name(self: MultiBlock, index: int) -> str | None:
        """Return the string name of the block at the given index.

        Parameters
        ----------
        index : int
            Index of the block to get the name of.

        Returns
        -------
        str
            Name of the block at the given index.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.get_block_name(0)
        'cube'

        """
        index = range(self.n_blocks)[index]
        meta = self.GetMetaData(index)
        if meta is not None:
            return meta.Get(_vtk.vtkCompositeDataSet.NAME())
        return None

    def keys(self: MultiBlock) -> list[str | None]:
        """Get all the block names in the dataset.

        Returns
        -------
        list
            List of block names.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.keys()
        ['cube', 'sphere']

        """
        return [self.get_block_name(i) for i in range(self.n_blocks)]

    def _ipython_key_completions_(self: MultiBlock) -> list[str | None]:
        return self.keys()

    def replace(self: MultiBlock, index: int, dataset: _TypeMultiBlockLeaf) -> None:
        """Replace dataset at index while preserving key name.

        Parameters
        ----------
        index : int
            Index of the block to replace.
        dataset : pyvista.DataSet or pyvista.MultiBlock
            Dataset for replacing the one at index.

        Examples
        --------
        >>> import pyvista as pv
        >>> import numpy as np
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.replace(1, pv.Sphere(center=(10, 10, 10)))
        >>> blocks.keys()
        ['cube', 'sphere']
        >>> np.allclose(blocks[1].center, [10.0, 10.0, 10.0])
        True

        """
        name = self.get_block_name(index)
        self[index] = dataset
        self.set_block_name(index, name)

    @overload
    def __setitem__(
        self: MultiBlock,
        index: int | str,
        data: _TypeMultiBlockLeaf,
    ) -> None: ...  # pragma: no cover

    @overload
    def __setitem__(
        self: MultiBlock,
        index: slice,
        data: Iterable[_TypeMultiBlockLeaf],
    ) -> None: ...  # pragma: no cover

    def __setitem__(
        self: MultiBlock,
        index: int | str | slice,
        data,
    ) -> None:
        """Set a block with a VTK data object.

        To set the name simultaneously, pass a string name as the 2nd index.

        Examples
        --------
        >>> import pyvista as pv
        >>> multi = pv.MultiBlock()
        >>> multi.append(pv.PolyData())
        >>> multi[0] = pv.UnstructuredGrid()
        >>> multi.append(pv.PolyData(), 'poly')
        >>> multi.keys()
        ['Block-00', 'poly']
        >>> multi['bar'] = pv.PolyData()
        >>> multi.n_blocks
        3

        """
        i: int = 0
        name: str | None = None
        if isinstance(index, str):
            try:
                i = self.get_index_by_name(index)
            except KeyError:
                self.append(data, index)
                return
            name = index
        elif isinstance(index, slice):
            index_iter = range(self.n_blocks)[index]
            for i, (idx, d) in enumerate(zip_longest(index_iter, data)):
                if idx is None:
                    self.insert(
                        index_iter[-1] + 1 + (i - len(index_iter)),
                        d,
                    )  # insert after last entry, increasing
                elif d is None:
                    del self[index_iter[-1] + 1]  # delete next entry
                else:
                    self[idx] = d  #
            return
        else:
            i = index

        # data, i, and name are a single value now
        data = cast(pyvista.DataSet, wrap(data))

        i = range(self.n_blocks)[i]

        # this is the only spot in the class where we actually add
        # data to the MultiBlock

        # check if we are overwriting a block
        existing_dataset = self.GetBlock(i)
        if existing_dataset is not None:
            self._remove_ref(i)
        self.SetBlock(i, data)
        if data is not None:
            self._refs[data.memory_address] = data

        if name is None:
            name = f'Block-{i:02}'
        self.set_block_name(i, name)  # Note that this calls self.Modified()

    def __delitem__(self: MultiBlock, index: int | str | slice) -> None:
        """Remove a block at the specified index."""
        if isinstance(index, slice):
            if index.indices(self.n_blocks)[2] > 0:
                for i in reversed(range(*index.indices(self.n_blocks))):
                    self.__delitem__(i)
            else:
                for i in range(*index.indices(self.n_blocks)):
                    self.__delitem__(i)
            return
        if isinstance(index, str):
            index = self.get_index_by_name(index)
        self._remove_ref(index)
        self.RemoveBlock(index)

    def _remove_ref(self: MultiBlock, index: int) -> None:
        """Remove python reference to the dataset."""
        dataset = self[index]
        if hasattr(dataset, 'memory_address'):
            self._refs.pop(dataset.memory_address, None)  # type: ignore[union-attr]

    def __eq__(self: MultiBlock, other: object) -> bool:
        """Equality comparison."""
        if not isinstance(other, MultiBlock):
            return False

        if self is other:
            return True

        if len(self) != len(other):
            return False

        if not self.keys() == other.keys():
            return False

        return not any(self_mesh != other_mesh for self_mesh, other_mesh in zip(self, other))

    def insert(
        self: MultiBlock, index: int, dataset: _TypeMultiBlockLeaf, name: str | None = None
    ) -> None:
        """Insert data before index.

        Parameters
        ----------
        index : int
            Index before which to insert data.
        dataset : pyvista.DataSet or pyvista.MultiBlock
            Data to insert.
        name : str, optional
            Name for key to give dataset.  A default name is given
            depending on the block index as ``'Block-{i:02}'``.

        Examples
        --------
        Insert a new :class:`pyvista.PolyData` at the start of the multiblock.

        >>> import pyvista as pv
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.keys()
        ['cube', 'sphere']
        >>> blocks.insert(0, pv.Plane(), 'plane')
        >>> blocks.keys()
        ['plane', 'cube', 'sphere']

        """
        index = range(self.n_blocks)[index]

        self.n_blocks += 1
        for i in reversed(range(index, self.n_blocks - 1)):
            self[i + 1] = self[i]
            self.set_block_name(i + 1, self.get_block_name(i))

        self[index] = dataset
        self.set_block_name(index, name)

    def pop(self: MultiBlock, index: int | str = -1) -> _TypeMultiBlockLeaf:
        """Pop off a block at the specified index.

        Parameters
        ----------
        index : int or str, default: -1
            Index or name of the dataset within the multiblock.  Defaults to
            last dataset.

        Returns
        -------
        pyvista.DataSet or pyvista.MultiBlock
            Dataset from the given index that was removed.

        Examples
        --------
        Pop the ``"cube"`` multiblock.

        >>> import pyvista as pv
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.keys()
        ['cube', 'sphere']
        >>> cube = blocks.pop('cube')
        >>> blocks.keys()
        ['sphere']

        """
        if isinstance(index, int):
            index = range(self.n_blocks)[index]
        data = self[index]
        del self[index]
        return data

    def reverse(self: MultiBlock) -> None:
        """Reverse MultiBlock in-place.

        Examples
        --------
        Reverse a multiblock.

        >>> import pyvista as pv
        >>> data = {
        ...     'cube': pv.Cube(),
        ...     'sphere': pv.Sphere(center=(2, 2, 0)),
        ... }
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.keys()
        ['cube', 'sphere']
        >>> blocks.reverse()
        >>> blocks.keys()
        ['sphere', 'cube']

        """
        # Taken from implementation in collections.abc.MutableSequence
        names = self.keys()
        n = len(self)
        for i in range(n // 2):
            self[i], self[n - i - 1] = self[n - i - 1], self[i]
        for i, name in enumerate(reversed(names)):
            self.set_block_name(i, name)

    def clean(self: MultiBlock, empty: bool = True) -> None:
        """Remove any null blocks in place.

        Parameters
        ----------
        empty : bool, default: True
            Remove any meshes that are empty as well (have zero points).

        Examples
        --------
        >>> import pyvista as pv
        >>> data = {'cube': pv.Cube(), 'empty': pv.PolyData()}
        >>> blocks = pv.MultiBlock(data)
        >>> blocks.clean(empty=True)
        >>> blocks.keys()
        ['cube']

        """
        null_blocks = []
        for i in range(self.n_blocks):
            data = self[i]
            if isinstance(data, MultiBlock):
                # Recursively move through nested structures
                data.clean()
                if data.n_blocks < 1:
                    null_blocks.append(i)
            elif data is None or empty and data.n_points < 1:
                null_blocks.append(i)
        # Now remove the null/empty meshes
        null_blocks = np.array(null_blocks, dtype=int)  # type: ignore[assignment]
        for i in range(len(null_blocks)):
            # Cast as int because windows is super annoying
            del self[int(null_blocks[i])]
            null_blocks -= 1  # type: ignore[assignment, operator]

    def _get_attrs(self: MultiBlock) -> list[tuple[str, Any, str]]:
        """Return the representation methods (internal helper)."""
        attrs: list[tuple[str, Any, str]] = []
        attrs.append(('N Blocks:', self.n_blocks, '{}'))
        bds = self.bounds
        attrs.append(('X Bounds:', (bds.x_min, bds.x_max), '{:.3e}, {:.3e}'))
        attrs.append(('Y Bounds:', (bds.y_min, bds.y_max), '{:.3e}, {:.3e}'))
        attrs.append(('Z Bounds:', (bds.z_min, bds.z_max), '{:.3e}, {:.3e}'))
        return attrs

    def _repr_html_(self: MultiBlock) -> str:
        """Define a pretty representation for Jupyter notebooks."""
        fmt = ''
        fmt += "<table style='width: 100%;'>"
        fmt += '<tr><th>Information</th><th>Blocks</th></tr>'
        fmt += '<tr><td>'
        fmt += '\n'
        fmt += '<table>\n'
        fmt += f'<tr><th>{type(self).__name__}</th><th>Values</th></tr>\n'
        row = '<tr><td>{}</td><td>{}</td></tr>\n'

        # now make a call on the object to get its attributes as a list of len 2 tuples
        for attr in self._get_attrs():
            try:
                fmt += row.format(attr[0], attr[2].format(*attr[1]))
            except:
                fmt += row.format(attr[0], attr[2].format(attr[1]))

        fmt += '</table>\n'
        fmt += '\n'
        fmt += '</td><td>'
        fmt += '\n'
        fmt += '<table>\n'
        row = '<tr><th>{}</th><th>{}</th><th>{}</th></tr>\n'
        fmt += row.format('Index', 'Name', 'Type')

        for i in range(self.n_blocks):
            data = self[i]
            fmt += row.format(i, self.get_block_name(i), type(data).__name__)

        fmt += '</table>\n'
        fmt += '\n'
        fmt += '</td></tr> </table>'
        return fmt

    def __repr__(self: MultiBlock) -> str:
        """Define an adequate representation."""
        # return a string that is Python console friendly
        fmt = f'{type(self).__name__} ({hex(id(self))})\n'
        # now make a call on the object to get its attributes as a list of len 2 tuples
        max_len = max(len(attr[0]) for attr in self._get_attrs()) + 3
        row = '  {:%ds}{}\n' % max_len
        for attr in self._get_attrs():
            try:
                fmt += row.format(attr[0], attr[2].format(*attr[1]))
            except:
                fmt += row.format(attr[0], attr[2].format(attr[1]))
        return fmt.strip()

    def __str__(self: MultiBlock) -> str:
        """Return the str representation of the multi block."""
        return MultiBlock.__repr__(self)

    def __len__(self: MultiBlock) -> int:
        """Return the number of blocks."""
        return self.n_blocks

    def copy_meta_from(
        self: MultiBlock, ido: MultiBlock, deep: bool
    ) -> None:  # numpydoc ignore=PR01
        """Copy pyvista meta data onto this object from another object."""
        # Note that `pyvista.MultiBlock` datasets currently don't have any meta.
        # This method is here for consistency with the rest of the API and
        # in case we add meta data to this pbject down the road.

    def copy(self: MultiBlock, deep: bool = True) -> MultiBlock:
        """Return a copy of the multiblock.

        Parameters
        ----------
        deep : bool, default: True
            When ``True``, make a full copy of the object.

        Returns
        -------
        pyvista.MultiBlock
           Deep or shallow copy of the ``MultiBlock``.

        Examples
        --------
        >>> import pyvista as pv
        >>> data = [
        ...     pv.Sphere(center=(2, 0, 0)),
        ...     pv.Cube(center=(0, 2, 0)),
        ...     pv.Cone(),
        ... ]
        >>> blocks = pv.MultiBlock(data)
        >>> new_blocks = blocks.copy()
        >>> len(new_blocks)
        3

        """
        thistype = type(self)
        newobject = thistype()
        if deep:
            newobject.deep_copy(self)
        else:
            newobject.shallow_copy(self)
        newobject.copy_meta_from(self, deep)
        return newobject

    def shallow_copy(  # type: ignore[override]
        self: MultiBlock, to_copy: _vtk.vtkMultiBlockDataSet, recursive: bool = False
    ) -> None:
        """Shallow copy the given multiblock to this multiblock.

        Parameters
        ----------
        to_copy : pyvista.MultiBlock or vtk.vtkMultiBlockDataSet
            Data object to perform a shallow copy from.

        recursive : bool, default: False
            Also shallow-copy any nested :class:`~pyvista.MultiBlock` blocks. By
            default, only the root :class:`~pyvista.MultiBlock` is shallow-copied and
            any nested multi-blocks are not shallow-copied.

        """
        if pyvista.vtk_version_info >= (9, 3):  # pragma: no cover
            self.CompositeShallowCopy(to_copy)
        else:
            self.ShallowCopy(to_copy)
        self.wrap_nested()

        # Shallow copy creates new instances of nested multiblocks
        # Iterate through the blocks to fix this recursively
        def _replace_nested_multiblocks(
            this_object_: MultiBlock, new_object: _vtk.vtkMultiBlockDataSet
        ) -> None:
            for i, this_block in enumerate(this_object_):
                if isinstance(this_block, _vtk.vtkMultiBlockDataSet):
                    block_to_copy = cast(MultiBlock, new_object.GetBlock(i))
                    this_object_.replace(i, block_to_copy)
                    _replace_nested_multiblocks(cast(MultiBlock, this_object_[i]), block_to_copy)

        if not recursive:
            _replace_nested_multiblocks(self, to_copy)

    def deep_copy(self: MultiBlock, to_copy: _vtk.vtkMultiBlockDataSet) -> None:  # type: ignore[override]
        """Overwrite this MultiBlock with another MultiBlock as a deep copy.

        Parameters
        ----------
        to_copy : pyvista.MultiBlock or vtk.vtkMultiBlockDataSet
            MultiBlock to perform a deep copy from.

        """
        super().deep_copy(to_copy)
        self.wrap_nested()

        # Deep copy will not copy the block name for None blocks (name is set to None instead)
        # Iterate through the blocks to fix this recursively
        def _set_name_for_none_blocks(
            this_object_: MultiBlock, new_object_: _vtk.vtkMultiBlockDataSet
        ) -> None:
            new_object_ = pyvista.wrap(new_object_)
            for i, dataset in enumerate(new_object_):
                if dataset is None:
                    this_object_.set_block_name(i, new_object_.get_block_name(i))
                elif isinstance(dataset, MultiBlock):
                    _set_name_for_none_blocks(cast(MultiBlock, this_object_[i]), dataset)

        _set_name_for_none_blocks(self, to_copy)

    def set_active_scalars(
        self: MultiBlock,
        name: str | None,
        preference: PointLiteral | CellLiteral = 'cell',
        allow_missing: bool = False,
    ) -> tuple[FieldAssociation, NumpyArray[float]]:
        """Find the scalars by name and appropriately set it as active.

        To deactivate any active scalars, pass ``None`` as the ``name``.

        Parameters
        ----------
        name : str or None
            Name of the scalars array to assign as active.  If
            ``None``, deactivates active scalars for both point and
            cell data.

        preference : str, default: "cell"
            If there are two arrays of the same name associated with
            points or cells, it will prioritize an array matching this
            type.  Can be either ``'cell'`` or ``'point'``.

        allow_missing : bool, default: False
            Allow missing scalars in part of the composite dataset. If all
            blocks are missing the array, it will raise a ``KeyError``.

        Returns
        -------
        pyvista.core.utilities.arrays.FieldAssociation
            Field association of the scalars activated.

        numpy.ndarray
            An array from the dataset matching ``name``.

        Notes
        -----
        The number of components of the data must match.

        """
        data_assoc: list[tuple[FieldAssociation, NumpyArray[float], _TypeMultiBlockLeaf]] = []
        for block in self:
            if block is not None:
                if isinstance(block, MultiBlock):
                    field, scalars = block.set_active_scalars(
                        name,
                        preference,
                        allow_missing=allow_missing,
                    )
                else:
                    try:
                        field, scalars_out = block.set_active_scalars(name, preference)
                        if scalars_out is None:
                            field, scalars = FieldAssociation.NONE, pyvista_ndarray([])
                        else:
                            scalars = scalars_out
                    except KeyError:
                        if not allow_missing:
                            raise
                        block.set_active_scalars(None, preference)
                        field, scalars = FieldAssociation.NONE, pyvista_ndarray([])

                if field != FieldAssociation.NONE:
                    data_assoc.append((field, scalars, block))

        if name is None:
            return FieldAssociation.NONE, pyvista_ndarray([])

        if not data_assoc:
            raise KeyError(f'"{name}" is missing from all the blocks of this composite dataset.')

        field_asc = data_assoc[0][0]
        # set the field association to the preference if at least one occurrence
        # of it exists
        preference_ = parse_field_choice(preference)
        if field_asc != preference_:
            for field, _, _ in data_assoc:
                if field == preference_:
                    field_asc = preference_
                    break

        # Verify array consistency
        dims: set[int] = set()
        dtypes: set[np.dtype[Any]] = set()
        for _ in self:
            for field, scalars, _ in data_assoc:
                # only check for the active field association
                if field != field_asc:
                    continue
                dims.add(scalars.ndim)
                dtypes.add(scalars.dtype)

        if len(dims) > 1:
            raise ValueError(f'Inconsistent dimensions {dims} in active scalars.')

        # check complex mismatch
        is_complex = [np.issubdtype(dtype, np.complexfloating) for dtype in dtypes]
        if any(is_complex) and not all(is_complex):
            raise ValueError('Inconsistent complex and real data types in active scalars.')

        return field_asc, scalars

    def as_polydata_blocks(self: MultiBlock, copy: bool = False) -> MultiBlock:
        """Convert all the datasets within this MultiBlock to :class:`pyvista.PolyData`.

        Parameters
        ----------
        copy : bool, default: False
            Option to create a shallow copy of any datasets that are already a
            :class:`pyvista.PolyData`. When ``False``, any datasets that are
            already PolyData will not be copied.

        Returns
        -------
        pyvista.MultiBlock
            MultiBlock containing only :class:`pyvista.PolyData` datasets.

        Notes
        -----
        Null blocks are converted to empty :class:`pyvista.PolyData`
        objects. Downstream filters that operate on PolyData cannot accept
        MultiBlocks with null blocks.

        """
        # we make a shallow copy here to avoid modifying the original dataset
        dataset = self.copy(deep=False)

        # Loop through the multiblock and convert to polydata
        for i, block in enumerate(dataset):
            if block is not None:
                if isinstance(block, MultiBlock):
                    dataset.replace(i, block.as_polydata_blocks(copy=copy))
                elif isinstance(block, pyvista.PointSet):
                    dataset.replace(i, block.cast_to_polydata(deep=True))
                elif not isinstance(block, pyvista.PolyData):
                    dataset.replace(i, block.extract_surface())
                elif copy:
                    # dataset is a PolyData
                    dataset.replace(i, block.copy(deep=False))
            else:
                # must have empty polydata within these datasets as some
                # downstream filters don't work on null pointers (i.e. None)
                dataset[i] = pyvista.PolyData()

        return dataset

    @property
    def is_all_polydata(self: MultiBlock) -> bool:
        """Return ``True`` when all the blocks are :class:`pyvista.PolyData`.

        This method will recursively check if any internal blocks are also
        :class:`pyvista.PolyData`.

        Returns
        -------
        bool
            Return ``True`` when all blocks are :class:`pyvista.PolyData`.

        """
        for block in self:
            if isinstance(block, MultiBlock):
                if not block.is_all_polydata:
                    return False
            else:
                if not isinstance(block, pyvista.PolyData):
                    return False

        return True

    def _activate_plotting_scalars(
        self: MultiBlock,
        scalars_name: str,
        preference: PointLiteral | CellLiteral,
        component: int | None,
        rgb: NumpyArray[float],
    ) -> tuple[FieldAssociation, str, np.dtype[np.number[Any]]]:
        """Active a scalars for an instance of :class:`pyvista.Plotter`."""
        # set the active scalars
        field, scalars = self.set_active_scalars(
            scalars_name,
            preference,
            allow_missing=True,
        )

        data_attr = f'{field.name.lower()}_data'
        dtype = scalars.dtype
        if rgb:
            if scalars.ndim != 2 or scalars.shape[1] not in (3, 4):
                raise ValueError('RGB array must be n_points/n_cells by 3/4 in shape.')
            if dtype != np.uint8:
                # uint8 is required by the mapper to display correctly
                _validation.check_subdtype(scalars, (np.floating, np.integer), name='rgb scalars')
                scalars_name = self._convert_to_uint8_rgb_scalars(data_attr, scalars_name)
        elif np.issubdtype(scalars.dtype, np.complexfloating):
            # Use only the real component if an array is complex
            scalars_name = self._convert_to_real_scalars(data_attr, scalars_name)
        elif scalars.dtype in (np.bool_, np.uint8):
            # bool and uint8 do not display properly, must convert to float
            self._convert_to_real_scalars(data_attr, scalars_name)
            if scalars.dtype == np.bool_:
                dtype = np.bool_  # type: ignore[assignment]
        elif scalars.ndim > 1:
            # multi-component
            if not isinstance(component, (int, type(None))):
                raise TypeError('`component` must be either None or an integer')
            if component is not None:
                if component >= scalars.shape[1] or component < 0:
                    raise ValueError(
                        'Component must be nonnegative and less than the '
                        f'dimensionality of the scalars array: {scalars.shape[1]}',
                    )
            scalars_name = self._convert_to_single_component(data_attr, scalars_name, component)

        return field, scalars_name, dtype

    def _convert_to_real_scalars(self: MultiBlock, data_attr: str, scalars_name: str) -> str:
        """Extract the real component of the active scalars of this dataset."""
        for block in self:
            if isinstance(block, MultiBlock):
                block._convert_to_real_scalars(data_attr, scalars_name)
            elif block is not None:
                scalars = getattr(block, data_attr).get(scalars_name, None)
                if scalars is not None:
                    scalars = np.array(scalars.astype(float))
                    dattr = getattr(block, data_attr)
                    dattr[f'{scalars_name}-real'] = scalars
                    dattr.active_scalars_name = f'{scalars_name}-real'
        return f'{scalars_name}-real'

    def _convert_to_uint8_rgb_scalars(self: MultiBlock, data_attr: str, scalars_name: str) -> str:
        """Convert rgb float or int scalars to uint8."""
        for block in self:
            if isinstance(block, MultiBlock):
                block._convert_to_uint8_rgb_scalars(data_attr, scalars_name)
            elif block is not None:
                scalars = getattr(block, data_attr).get(scalars_name, None)
                if scalars is not None:
                    if np.issubdtype(scalars.dtype, np.floating):
                        _validation.check_range(scalars, [0.0, 1.0], name='rgb float scalars')
                        scalars = np.array(scalars, dtype=np.uint8) * 255
                    elif np.issubdtype(scalars.dtype, np.integer):
                        _validation.check_range(scalars, [0, 255], name='rgb int scalars')
                        scalars = np.array(scalars, dtype=np.uint8)
                    dattr = getattr(block, data_attr)
                    dattr[f'{scalars_name}-uint8'] = scalars
                    dattr.active_scalars_name = f'{scalars_name}-uint8'
        return f'{scalars_name}-uint8'

    def _convert_to_single_component(
        self: MultiBlock,
        data_attr: str,
        scalars_name: str,
        component: int | None,
    ) -> str:
        """Convert multi-component scalars to a single component."""
        if component is None:
            for block in self:
                if isinstance(block, MultiBlock):
                    block._convert_to_single_component(data_attr, scalars_name, component)
                elif block is not None:
                    scalars = getattr(block, data_attr).get(scalars_name, None)
                    if scalars is not None:
                        scalars = np.linalg.norm(scalars, axis=1)
                        dattr = getattr(block, data_attr)
                        dattr[f'{scalars_name}-normed'] = scalars
                        dattr.active_scalars_name = f'{scalars_name}-normed'
            return f'{scalars_name}-normed'

        for block in self:
            if isinstance(block, MultiBlock):
                block._convert_to_single_component(data_attr, scalars_name, component)
            elif block is not None:
                scalars = getattr(block, data_attr).get(scalars_name, None)
                if scalars is not None:
                    dattr = getattr(block, data_attr)
                    dattr[f'{scalars_name}-{component}'] = scalars[:, component]
                    dattr.active_scalars_name = f'{scalars_name}-{component}'
        return f'{scalars_name}-{component}'

    def _get_consistent_active_scalars(self: MultiBlock) -> tuple[str | None, str | None]:
        """Get if there are any consistent active scalars."""
        point_names = set()
        cell_names = set()
        for block in self:
            if block is not None:
                if isinstance(block, MultiBlock):
                    point_name, cell_name = block._get_consistent_active_scalars()
                else:
                    point_name = block.point_data.active_scalars_name
                    cell_name = block.cell_data.active_scalars_name
                point_names.add(point_name)
                cell_names.add(cell_name)

        point_name = point_names.pop() if len(point_names) == 1 else None
        cell_name = cell_names.pop() if len(cell_names) == 1 else None
        return point_name, cell_name

    def clear_all_data(self: MultiBlock) -> None:
        """Clear all data from all blocks."""
        for block in self:
            if isinstance(block, MultiBlock):
                block.clear_all_data()
            elif block is not None:
                block.clear_data()

    def clear_all_point_data(self: MultiBlock) -> None:
        """Clear all point data from all blocks."""
        for block in self:
            if isinstance(block, MultiBlock):
                block.clear_all_point_data()
            elif block is not None:
                block.clear_point_data()

    def clear_all_cell_data(self: MultiBlock) -> None:
        """Clear all cell data from all blocks."""
        for block in self:
            if isinstance(block, MultiBlock):
                block.clear_all_cell_data()
            elif block is not None:
                block.clear_cell_data()
