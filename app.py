
import re
import unicodedata
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(page_title='Buscador eleitoral | POC', page_icon='🔎', layout='wide')
BASE = Path(__file__).resolve().parent / 'despesas_contratadas_candidatos_2026_AL.csv'
PREFERIDAS = ['NM_CANDIDATO', 'NR_CPF_CANDIDATO', 'NM_FORNECEDOR', 'NM_FORNECEDOR_RFB', 'NR_CPF_CNPJ_FORNECEDOR']


def normalizar(valor):
    if pd.isna(valor):
        return ''
    s = unicodedata.normalize('NFKD', str(valor).strip().upper())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    if re.fullmatch(r'[\d.\-/\s]+', s):
        s = re.sub(r'\D', '', s)
    return s


@st.cache_data(show_spinner='Carregando a base eleitoral...')
def carregar_base():
    con = duckdb.connect()
    try:
        try:
            df = con.execute('''
                SELECT * FROM read_csv(?, delim=';', header=true,
                    encoding='latin-1', all_varchar=true, sample_size=-1)
            ''', [str(BASE)]).df()
        except duckdb.Error:
            df = pd.read_csv(BASE, sep=';', encoding='latin-1', dtype=str, low_memory=False)
    finally:
        con.close()
    return df.fillna('').reset_index(drop=True)


@st.cache_data(show_spinner=False)
def coluna_normalizada(nome_campo):
    return carregar_base()[nome_campo].map(normalizar)


def corresponde(serie, termo, modo):
    if modo == 'Exata':
        return serie.eq(termo)
    if modo == 'Contém trecho':
        return serie.str.contains(termo, regex=False, na=False)
    palavras = termo.split()
    return pd.concat([serie.str.contains(p, regex=False, na=False) for p in palavras], axis=1).all(axis=1)


st.title('🔎 Buscador de despesas eleitorais')
st.caption('Prova de conceito • Alagoas • Eleições 2026 • Busca textual sem IA')
if not BASE.is_file():
    st.error('Arquivo CSV da base não encontrado na pasta do aplicativo.')
    st.stop()

try:
    base = carregar_base()
except Exception as exc:
    st.error(f'Não foi possível ler a base: {exc}')
    st.stop()

st.info(f'Base disponível: {len(base):,} registros e {len(base.columns)} colunas.'.replace(',', '.'))
modo = st.radio('Tipo de correspondência', ['Exata', 'Contém trecho', 'Todas as palavras'], horizontal=True)
campos_padrao = [c for c in PREFERIDAS if c in base.columns]
campos = st.multiselect('Campos em que pesquisar', options=base.columns.tolist(), default=campos_padrao)
entrada = st.text_area('Cole nomes, CPFs ou CNPJs (um por linha, separados por ; ou tab)', height=190,
    placeholder='EDILSON DE LIMA SANTOS\nSILVA\n123.456.789-01')

if st.button('Buscar na base', type='primary', use_container_width=True):
    if not campos:
        st.warning('Selecione pelo menos uma coluna.')
        st.stop()

    termos_originais = [x.strip() for x in re.split(r'[;\t\r\n]+', entrada) if x.strip()]
    termos = {}
    for original in termos_originais:
        chave = normalizar(original)
        if chave:
            termos.setdefault(chave, original)
    if not termos:
        st.warning('Cole pelo menos um nome, termo ou CPF/CNPJ.')
        st.stop()
    if len(termos) > 2000:
        st.warning('Limite de 2.000 termos por consulta nesta POC.')
        st.stop()

    partes = []
    encontrados = set()
    with st.spinner('Pesquisando na base...'):
        for campo in campos:
            norm = coluna_normalizada(campo)
            for chave, original in termos.items():
                mascara = corresponde(norm, chave, modo)
                indices = norm.index[mascara]
                if len(indices):
                    encontrados.add(chave)
                    partes.append(pd.DataFrame({
                        '_linha': indices,
                        'termo_pesquisado': original,
                        'campo_encontrado': campo,
                    }))
        if partes:
            achados = pd.concat(partes, ignore_index=True)
            linhas = base.iloc[achados['_linha'].to_numpy()].reset_index(drop=True)
            resultado = pd.concat([
                achados[['termo_pesquisado', 'campo_encontrado']].reset_index(drop=True),
                linhas
            ], axis=1)
        else:
            resultado = pd.DataFrame()
        ausentes = [original for chave, original in termos.items() if chave not in encontrados]

    st.write(f'**Termos pesquisados:** {len(termos)}  |  **Correspondências:** {len(resultado)}  |  **Termos sem resultado:** {len(ausentes)}')
    if not resultado.empty:
        st.subheader('Resultados')
        st.caption('Um registro pode aparecer mais de uma vez quando corresponde a vários termos ou campos.')
        st.dataframe(resultado, use_container_width=True, hide_index=True)
        st.download_button('Baixar resultados CSV',
            data=resultado.to_csv(index=False, sep=';').encode('utf-8-sig'),
            file_name='resultados_busca.csv', mime='text/csv')
    else:
        st.info('Nenhuma correspondência encontrada para os campos e o modo selecionados.')
    if ausentes:
        with st.expander('Ver termos não encontrados'):
            st.write('\n'.join(f'- {t}' for t in ausentes))
            st.download_button('Baixar termos não encontrados',
                data=pd.DataFrame({'termo_nao_encontrado': ausentes}).to_csv(index=False).encode('utf-8-sig'),
                file_name='nao_encontrados.csv', mime='text/csv')

with st.expander('Sobre a busca e os dados'):
    st.write('Exata: igualdade integral do campo. Contém trecho: sequência de caracteres em qualquer posição. Todas as palavras: cada palavra deve aparecer no campo, em qualquer ordem.')
    st.write('As buscas ignoram diferenças entre maiúsculas/minúsculas e acentos. Documentos numéricos são comparados sem pontuação.')
    st.write('Valores especiais como -1 e -4 podem aparecer na base e não devem ser interpretados como CPF válido.')
    st.write('A base permanece no servidor; os usuários não precisam enviar arquivos.')

