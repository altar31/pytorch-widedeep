import torch
from torch import nn

from pytorch_widedeep.wdtypes import Dict, List, Tuple, Tensor, Optional
from pytorch_widedeep.models.tabular.mlp._layers import MLP
from pytorch_widedeep.models.tabular._base_tabular_model import (
    BaseTabularModelWithAttention,
)
from pytorch_widedeep.models.tabular.transformers._encoders import (
    TransformerEncoder,
)


class TabTransformer(BaseTabularModelWithAttention):
    r"""Defines a [TabTransformer model](https://arxiv.org/abs/2012.06678) that
    can be used as the `deeptabular` component of a Wide & Deep model or
    independently by itself.

    :information_source: **NOTE**:
    This is an enhanced adaptation of the model described in the paper,
    containing a series of additional features.

    Parameters
    ----------
    column_idx: Dict
        Dict containing the index of the columns that will be passed through
        the model. Required to slice the tensors. e.g.
        _{'education': 0, 'relationship': 1, 'workclass': 2, ...}_
    cat_embed_input: List, Optional, default = None
        List of Tuples with the column name and number of unique values for
        each categorical component e.g. _[(education, 11), ...]_
    cat_embed_dropout: float, default = 0.1
        Categorical embeddings dropout
    use_cat_bias: bool, default = False,
        Boolean indicating if bias will be used for the categorical embeddings
    cat_embed_activation: Optional, str, default = None,
        Activation function for the categorical embeddings, if any. _'tanh'_,
        _'relu'_, _'leaky_relu'_ and _'gelu'_ are supported.
    full_embed_dropout: bool, default = False
        Boolean indicating if an entire embedding (i.e. the representation of
        one column) will be dropped in the batch. See:
        `pytorch_widedeep.models.transformers._layers.FullEmbeddingDropout`.
        If `full_embed_dropout = True`, `cat_embed_dropout` is ignored.
    shared_embed: bool, default = False
        The idea behind `shared_embed` is described in the Appendix A in the
        [TabTransformer paper](https://arxiv.org/abs/2012.06678): the
        goal of having column embedding is to enable the model to distinguish
        the classes in one column from those in the other columns. In other
        words, the idea is to let the model learn which column is embedded at
        the time.
    add_shared_embed: bool, default = False,
        The two embedding sharing strategies are: 1) add the shared embeddings
        to the column embeddings or 2) to replace the first
        `frac_shared_embed` with the shared embeddings.
        See `pytorch_widedeep.models.transformers._layers.SharedEmbeddings`
    frac_shared_embed: float, default = 0.25
        The fraction of embeddings that will be shared (if `add_shared_embed
        = False`) by all the different categories for one particular
        column.
    continuous_cols: List, Optional, default = None
        List with the name of the numeric (aka continuous) columns
    cont_norm_layer: str, default =  "batchnorm"
        Type of normalization layer applied to the continuous features. Options
        are: _'layernorm'_, _'batchnorm'_ or None.
    embed_continuous: bool, default = False
        Boolean indicating if the continuous columns will be embedded
        (i.e. passed each through a linear layer with or without activation)
    cont_embed_dropout: float, default = 0.1,
        Continuous embeddings dropout
    use_cont_bias: bool, default = True,
        Boolean indicating if bias will be used for the continuous embeddings
    cont_embed_activation: str, default = None
        Activation function to be applied to the continuous embeddings, if
        any. _'tanh'_, _'relu'_, _'leaky_relu'_ and _'gelu'_ are supported.
    input_dim: int, default = 32
        The so-called *dimension of the model*. Is the number of
        embeddings used to encode the categorical and/or continuous columns
    n_heads: int, default = 8
        Number of attention heads per Transformer block
    use_qkv_bias: bool, default = False
        Boolean indicating whether or not to use bias in the Q, K, and V
        projection layers.
    n_blocks: int, default = 4
        Number of Transformer blocks
    attn_dropout: float, default = 0.2
        Dropout that will be applied to the Multi-Head Attention layers
    ff_dropout: float, default = 0.1
        Dropout that will be applied to the FeedForward network
    ff_factor: float, default = 4
        Multiplicative factor applied to the first layer of the FF network in
        each Transformer block, This is normally set to 4.
    transformer_activation: str, default = "gelu"
        Transformer Encoder activation function. _'tanh'_, _'relu'_,
        _'leaky_relu'_, _'gelu'_, _'geglu'_ and _'reglu'_ are supported
    use_linear_attention: Boolean, default = False,
        Boolean indicating if Linear Attention from [Transformers are RNNs:
        Fast Autoregressive Transformers with Linear Attention]
        (https://arxiv.org/abs/2006.16236) will be used
    use_flash_attention: Boolean, default = False,
        Boolean indicating if [Flash Attention]
        (https://pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html)
        will be used
    mlp_hidden_dims: List, Optional, default = None
        MLP hidden dimensions. If not provided it will default to $[l,
        4\times l, 2 \times l]$ where $l$ is the MLP's input dimension
    mlp_activation: str, default = "relu"
        MLP activation function. _'tanh'_, _'relu'_, _'leaky_relu'_ and
        _'gelu'_ are supported
    mlp_dropout: float, default = 0.1
        Dropout that will be applied to the final MLP
    mlp_batchnorm: bool, default = False
        Boolean indicating whether or not to apply batch normalization to the
        dense layers
    mlp_batchnorm_last: bool, default = False
        Boolean indicating whether or not to apply batch normalization to the
        last of the dense layers
    mlp_linear_first: bool, default = False
        Boolean indicating whether the order of the operations in the dense
        layer. If `True: [LIN -> ACT -> BN -> DP]`. If `False: [BN -> DP ->
        LIN -> ACT]`

    Attributes
    ----------
    cat_and_cont_embed: nn.Module
        This is the module that processes the categorical and continuous columns
    encoder: nn.Module
        Sequence of Transformer blocks
    mlp: nn.Module
        MLP component in the model

    Examples
    --------
    >>> import torch
    >>> from pytorch_widedeep.models import TabTransformer
    >>> X_tab = torch.cat((torch.empty(5, 4).random_(4), torch.rand(5, 1)), axis=1)
    >>> colnames = ['a', 'b', 'c', 'd', 'e']
    >>> cat_embed_input = [(u,i) for u,i in zip(colnames[:4], [4]*4)]
    >>> continuous_cols = ['e']
    >>> column_idx = {k:v for v,k in enumerate(colnames)}
    >>> model = TabTransformer(column_idx=column_idx, cat_embed_input=cat_embed_input, continuous_cols=continuous_cols)
    >>> out = model(X_tab)
    """

    def __init__(
        self,
        column_idx: Dict[str, int],
        cat_embed_input: Optional[List[Tuple[str, int]]] = None,
        cat_embed_dropout: float = 0.1,
        use_cat_bias: bool = False,
        cat_embed_activation: Optional[str] = None,
        full_embed_dropout: bool = False,
        shared_embed: bool = False,
        add_shared_embed: bool = False,
        frac_shared_embed: float = 0.25,
        continuous_cols: Optional[List[str]] = None,
        cont_norm_layer: str = None,
        embed_continuous: bool = False,
        cont_embed_dropout: float = 0.1,
        use_cont_bias: bool = True,
        cont_embed_activation: Optional[str] = None,
        input_dim: int = 32,
        n_heads: int = 8,
        use_qkv_bias: bool = False,
        n_blocks: int = 4,
        attn_dropout: float = 0.2,
        ff_dropout: float = 0.1,
        ff_factor: int = 4,
        transformer_activation: str = "gelu",
        use_linear_attention: bool = False,
        use_flash_attention: bool = False,
        mlp_hidden_dims: Optional[List[int]] = None,
        # enabled_flash_backends,
        mlp_activation: str = "relu",
        mlp_dropout: float = 0.1,
        mlp_batchnorm: bool = False,
        mlp_batchnorm_last: bool = False,
        mlp_linear_first: bool = True,
    ):
        super(TabTransformer, self).__init__(
            column_idx=column_idx,
            cat_embed_input=cat_embed_input,
            cat_embed_dropout=cat_embed_dropout,
            use_cat_bias=use_cat_bias,
            cat_embed_activation=cat_embed_activation,
            full_embed_dropout=full_embed_dropout,
            shared_embed=shared_embed,
            add_shared_embed=add_shared_embed,
            frac_shared_embed=frac_shared_embed,
            continuous_cols=continuous_cols,
            cont_norm_layer=cont_norm_layer,
            embed_continuous=embed_continuous,
            cont_embed_dropout=cont_embed_dropout,
            use_cont_bias=use_cont_bias,
            cont_embed_activation=cont_embed_activation,
            input_dim=input_dim,
        )

        self.n_heads = n_heads
        self.use_qkv_bias = use_qkv_bias
        self.n_blocks = n_blocks
        self.attn_dropout = attn_dropout
        self.ff_dropout = ff_dropout
        self.transformer_activation = transformer_activation
        self.use_linear_attention = use_linear_attention
        self.use_flash_attention = use_flash_attention
        self.ff_factor = ff_factor

        self.mlp_hidden_dims = mlp_hidden_dims
        self.mlp_activation = mlp_activation
        self.mlp_dropout = mlp_dropout
        self.mlp_batchnorm = mlp_batchnorm
        self.mlp_batchnorm_last = mlp_batchnorm_last
        self.mlp_linear_first = mlp_linear_first

        self.with_cls_token = "cls_token" in column_idx
        self.n_cat = len(cat_embed_input) if cat_embed_input is not None else 0
        self.n_cont = len(continuous_cols) if continuous_cols is not None else 0

        if self.n_cont and not self.n_cat and not self.embed_continuous:
            raise ValueError(
                "If only continuous features are used 'embed_continuous' must be set to 'True'"
            )

        # Embeddings are instantiated at the base model
        # Transformer blocks
        self.encoder = nn.Sequential()
        for i in range(n_blocks):
            self.encoder.add_module(
                "transformer_block" + str(i),
                TransformerEncoder(
                    input_dim,
                    n_heads,
                    use_qkv_bias,
                    attn_dropout,
                    ff_dropout,
                    ff_factor,
                    transformer_activation,
                    use_linear_attention,
                    use_flash_attention,
                ),
            )

        self.mlp_first_hidden_dim = self._mlp_first_hidden_dim()

        if mlp_hidden_dims is not None:
            self.mlp = MLP(
                [self.mlp_first_hidden_dim] + mlp_hidden_dims,
                mlp_activation,
                mlp_dropout,
                mlp_batchnorm,
                mlp_batchnorm_last,
                mlp_linear_first,
            )
        else:
            self.mlp = None

    def forward(self, X: Tensor) -> Tensor:
        if not self.embed_continuous:
            x_cat, x_cont = self.cat_and_cont_embed(X)
            if x_cat is not None:
                x = (
                    self.cat_embed_act_fn(x_cat)
                    if self.cat_embed_act_fn is not None
                    else x_cat
                )
        else:
            x = self._get_embeddings(X)
            x_cont = None

        x = self.encoder(x)
        if self.with_cls_token:
            x = x[:, 0, :]
        else:
            x = x.flatten(1)

        if x_cont is not None and not self.embed_continuous:
            x = torch.cat([x, x_cont], 1)

        if self.mlp is not None:
            x = self.mlp(x)
        return x

    def _mlp_first_hidden_dim(self) -> int:
        if self.with_cls_token:
            if self.embed_continuous:
                attn_output_dim = self.input_dim
            else:
                attn_output_dim = self.input_dim + self.n_cont
        elif self.embed_continuous:
            attn_output_dim = (self.n_cat + self.n_cont) * self.input_dim
        else:
            attn_output_dim = self.n_cat * self.input_dim + self.n_cont

        return attn_output_dim

    @property
    def output_dim(self) -> int:
        r"""The output dimension of the model. This is a required property
        neccesary to build the `WideDeep` class
        """
        return (
            self.mlp_hidden_dims[-1]
            if self.mlp_hidden_dims is not None
            else self.mlp_first_hidden_dim
        )

    @property
    def attention_weights(self) -> List[Tensor]:
        r"""List with the attention weights per block

        The shape of the attention weights is $(N, H, F, F)$, where $N$ is the
        batch size, $H$ is the number of attention heads and $F$ is the
        number of features/columns in the dataset
        """
        if self.use_flash_attention or self.use_linear_attention:
            raise ValueError(
                "Extraction of the attention weights is not supported for "
                "linear or flash attention"
            )

        return [blk.attn.attn_weights for blk in self.encoder]

    def _compute_attn_output_dim(self) -> int:
        if self.with_cls_token:
            if self.embed_continuous:
                attn_output_dim = self.input_dim
            else:
                attn_output_dim = self.input_dim + self.n_cont
        elif self.embed_continuous:
            attn_output_dim = (self.n_cat + self.n_cont) * self.input_dim
        else:
            attn_output_dim = self.n_cat * self.input_dim + self.n_cont

        return attn_output_dim
