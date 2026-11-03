import io
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
    # DuckDB lê o CSV sem transformar CPF em número, preservando zeros à esquerda.
    con = duckdb.connect()
    try:
        try:
            df = con.execute('''
                SELECT * FROM read_csv(?, delim=';', header=true,
                  encoding='latin-1', all_varchar=true, sample_size=-1)
            ''', [str(BASE)]).df()
        except duckdb.Error:
            # Compatibilidade com versões do DuckDB que não reconheçam Latin-1.
            df = pd.read_csv(BASE, sep=';', encoding='latin-1', dtype=str, low_memory=False)
    finally:
        con.close()
    return df

st.title('🔎 Buscador de despesas eleitorais')
st.caption('Prova de conceito • Alagoas • Eleições 2026 • Busca exata, sem IA')
if not BASE.is_file():
    st.error('Arquivo CSV da base não encontrado na pasta do aplicativo.')
    st.stop()

try:
    base = carregar_base()
except Exception as exc:
    st.error(f'Não foi possível ler a base: {exc}')
    st.stop()

st.info(f'Base disponível: {len(base):,} registros e {len(base.columns)} colunas.'.replace(',', '.'))
campos_padrao = [c for c in PREFERIDAS if c in base.columns]
campos = st.multiselect('Campos em que pesquisar', options=base.columns.tolist(), default=campos_padrao)
entrada = st.text_area('Cole nomes, CPFs ou CNPJs (um por linha, separados por ; ou por tab)', height=190,
    placeholder='NOME COMPLETO\n123.456.789-01\nOUTRO NOME')
executar = st.button('Buscar na base', type='primary', use_container_width=True)

if executar:
    if not campos:
        st.warning('Selecione pelo menos uma coluna.')
        st.stop()
    termos_originais = [x.strip() for x in re.split(r'[;\t\r\n]+', entrada) if x.strip()]
    # Mantém o primeiro termo original de cada versão normalizada.
    termos = {}
    for original in termos_originais:
        chave = normalizar(original)
        if chave:
            termos.setdefault(chave, original)
    if not termos:
        st.warning('Cole pelo menos um nome ou CPF/CNPJ.')
        st.stop()
    if len(termos) > 2000:
        st.warning('Limite de 2.000 termos por consulta nesta POC.')
        st.stop()
    with st.spinner('Procurando correspondências exatas...'):
        # Índice de correspondências: somente as colunas escolhidas, com posição da linha.
        partes = []
        for campo in campos:
            norm = base[campo].map(normalizar)
            mascara = norm.isin(termos)
            if mascara.any():
                indices = norm.index[mascara]
                partes.append(pd.DataFrame({
                    '_linha': indices,
                    'termo_pesquisado': norm.loc[indices].map(termos).to_numpy(),
                    'campo_encontrado': campo,
                }))
        if partes:
            achados = pd.concat(partes, ignore_index=True)
            linhas = base.loc[achados['_linha'].to_numpy()].reset_index(drop=True)
            resultado = pd.concat([achados[['termo_pesquisado', 'campo_encontrado']].reset_index(drop=True), linhas], axis=1)
            encontrados = {normalizar(t) for t in achados['termo_pesquisado']}
        else:
            resultado = pd.DataFrame()
            encontrados = set()
        ausentes = [original for chave, original in termos.items() if chave not in encontrados]
    a, b, c = st.columns(3)
    a.metric('Termos pesquisados', len(termos))
    b.metric('Linhas correspondentes', len(resultado))
    c.metric('Termos não encontrados', len(ausentes))
    if not resultado.empty:
        st.subheader('Resultados')
        st.caption('Uma linha pode aparecer mais de uma vez se corresponder a mais de um campo ou termo.')
        st.dataframe(resultado, use_container_width=True, hide_index=True)
        st.download_button('Baixar resultados CSV', data=resultado.to_csv(index=False, sep=';').encode('utf-8-sig'),
                           file_name='resultados_busca.csv', mime='text/csv')
    else:
        st.info('Nenhuma correspondência exata encontrada.')
    if ausentes:
        with st.expander('Ver termos não encontrados'):
            st.dataframe(pd.DataFrame({'termo_nao_encontrado': ausentes}), hide_index=True)
            st.download_button('Baixar termos não encontrados', data=pd.DataFrame({'termo_nao_encontrado': ausentes}).to_csv(index=False).encode('utf-8-sig'),
                               file_name='nao_encontrados.csv', mime='text/csv')

with st.expander('Sobre a busca e os dados'):
    st.write('A busca exige igualdade integral no campo selecionado, após normalizar acentos e caixa. Para identificadores compostos apenas de dígitos e pontuação, remove pontos, barras e hífens. Não usa correspondência parcial, aproximação nem IA.')
    st.write('Valores especiais de ausência de dados, como -1 e -4, podem existir na base original. Não devem ser interpretados como CPF válido.')
    st.write('Os dados são carregados no servidor do aplicativo; não há upload pelos usuários.')
