#!/usr/bin/env python3
"""
PsiqMentor V5 - Backend API
Agente simulador de pacientes com Transtornos de Ansiedade para treinamento médico.
Mestrado em Ensino em Saúde - CESUPA

V5 - Mudanças (sobre a V4):
- Botão "Ouvir" nas respostas dos pacientes (TTS via ElevenLabs)
- Vozes distintas para cada um dos 9 pacientes
- Parser de respostas para separar texto comportamental (*gestos*) do diálogo falado
- Seletor de modo: Chat de Texto / Chat com Áudio
- Tudo em repositório separado, sem interferir na v4 publicada
"""

import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from anthropic import Anthropic
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

# TTS helper
from generate_audio import generate_audio

# ─── Load DSM-5 Knowledge Base ─────────────────────────────────────────────
DSM5_PATH = Path(__file__).parent / "dsm5_ansiedade.json"
with open(DSM5_PATH, "r", encoding="utf-8") as f:
    DSM5_DATA = json.load(f)

# ─── Supplementary DSM-5 criteria for disorders not in the JSON ──────────────
DSM5_DATA["transtornos_de_ansiedade"]["ansiedade_separacao"] = {
    "nome_completo": "Transtorno de Ansiedade de Separação",
    "codigo_cid": "F93.0",
    "criterios": {
        "A": {
            "descricao": "Medo ou ansiedade impróprios e excessivos em relação ao nível de desenvolvimento, envolvendo separação daqueles a quem o indivíduo é apegado, evidenciado por três (ou mais) dos seguintes:",
            "sintomas": {
                "A1": "Sofrimento excessivo e recorrente ante a ocorrência ou previsão de afastamento de casa ou de figuras importantes de apego.",
                "A2": "Preocupação persistente e excessiva acerca da possível perda das principais figuras de apego ou de perigos para elas (doença, ferimentos, catástrofes, morte).",
                "A3": "Preocupação persistente e excessiva de que um evento indesejado leve à separação de uma figura importante de apego (perder-se, ser sequestrado, ter acidente, ficar doente).",
                "A4": "Relutância persistente ou recusa a sair de casa, ir para a escola, trabalho ou qualquer outro lugar por causa do medo de separação.",
                "A5": "Temor persistente e excessivo ou relutância em ficar sozinho ou sem as principais figuras de apego em casa ou em outros contextos.",
                "A6": "Relutância ou recusa persistente em dormir fora de casa ou em dormir sem estar perto de uma figura importante de apego.",
                "A7": "Pesadelos repetidos envolvendo o tema de separação.",
                "A8": "Queixas repetidas de sintomas somáticos quando a separação das figuras importantes de apego ocorre ou é prevista."
            },
            "minimo_necessario": 3
        },
        "B": "O medo, a ansiedade ou a esquiva é persistente, durando pelo menos quatro semanas em crianças e adolescentes e geralmente seis meses ou mais em adultos.",
        "C": "A perturbação causa sofrimento clinicamente significativo ou prejuízo no funcionamento social, profissional ou em outras áreas importantes da vida do indivíduo.",
        "D": "A perturbação não é mais bem explicada por outro transtorno mental."
    }
}

DSM5_DATA["transtornos_de_ansiedade"]["mutismo_seletivo"] = {
    "nome_completo": "Mutismo Seletivo",
    "codigo_cid": "F94.0",
    "criterios": {
        "A": "Fracasso persistente para falar em situações sociais específicas nas quais existe expectativa para tal (p. ex., na escola), apesar de falar em outras situações.",
        "B": "A perturbação interfere na realização educacional ou profissional ou na comunicação social.",
        "C": "A duração mínima da perturbação é um mês (não se limita ao primeiro mês de escola).",
        "D": "O fracasso para falar não se deve a falta de conhecimento ou de conforto com o idioma exigido na situação social.",
        "E": "A perturbação não é mais bem explicada por um transtorno da comunicação e não ocorre exclusivamente durante o curso de TEA, esquizofrenia ou outro transtorno psicótico."
    }
}

DSM5_DATA["transtornos_de_ansiedade"]["ansiedade_substancia"] = {
    "nome_completo": "Transtorno de Ansiedade Induzido por Substância/Medicamento",
    "codigo_cid": "F19.980",
    "criterios": {
        "A": "Ataques de pânico ou ansiedade são predominantes no quadro clínico.",
        "B": {
            "descricao": "Existem evidências a partir da história, do exame físico ou de achados laboratoriais de ambos:",
            "B1": "Os sintomas do Critério A se desenvolveram durante ou logo após a intoxicação ou abstênência de substância, ou após exposição a um medicamento.",
            "B2": "A substância/medicamento envolvido é capaz de produzir os sintomas do Critério A."
        },
        "C": "A perturbação não é mais bem explicada por um transtorno de ansiedade não induzido por substância/medicamento.",
        "D": "A perturbação não ocorre exclusivamente durante o curso de delirium.",
        "E": "A perturbação causa sofrimento clinicamente significativo ou prejuízo no funcionamento social, profissional ou em outras áreas importantes."
    },
    "substancias_relevantes": ["cafeína", "álcool", "cannabis", "estimulantes", "descongestionantes", "broncodilatadores", "corticosteroides"]
}

DSM5_DATA["transtornos_de_ansiedade"]["ansiedade_medica"] = {
    "nome_completo": "Transtorno de Ansiedade Devido a Outra Condição Médica",
    "codigo_cid": "F06.4",
    "criterios": {
        "A": "Ataques de pânico ou ansiedade são predominantes no quadro clínico.",
        "B": "Há evidências a partir da história, do exame físico ou de achados laboratoriais de que a perturbação é a consequência fisiopatológica direta de outra condição médica.",
        "C": "A perturbação não é mais bem explicada por outro transtorno mental.",
        "D": "A perturbação não ocorre exclusivamente durante o curso de delirium.",
        "E": "A perturbação causa sofrimento clinicamente significativo ou prejuízo no funcionamento social, profissional ou em outras áreas importantes."
    },
    "condicoes_medicas_comuns": ["hipertireoidismo", "feocromocitoma", "hipoglicemia", "doenças cardiovasculares", "doenças pulmonares", "doenças vestibulares"]
}

# ─── Patient Profiles (1 per DSM-5-TR anxiety disorder) ─────────────────────
PATIENT_PROFILES = [
    # 1. TAG
    {
        "nome": "Márcia",
        "idade": 34,
        "genero": "feminino",
        "ocupacao": "professora de ensino fundamental",
        "estado_civil": "casada, dois filhos",
        "contexto": "Nos últimos 8 meses, Márcia tem apresentado preocupação constante com o desempenho dos filhos na escola, com as finanças da família e com a possibilidade de perder o emprego, apesar de ter estabilidade no cargo. Relata dificuldade em dormir (acorda várias vezes à noite com pensamentos sobre o futuro), tensão muscular frequente nos ombros e pescoço, fadiga constante mesmo após descanso, e irritabilidade que tem afetado seu casamento. Tem dificuldade de se concentrar nas aulas que ministra. Nega uso de substâncias.",
        "transtorno": "TAG",
        "criterios_key": "TAG",
        "diagnostico_real": "Transtorno de Ansiedade Generalizada (F41.1)",
    },
    # 2. Transtorno de Pânico
    {
        "nome": "Fernando",
        "idade": 28,
        "genero": "masculino",
        "ocupacao": "engenheiro de software",
        "estado_civil": "solteiro, mora com a namorada",
        "contexto": "Fernando procura atendimento após 4 meses de ataques recorrentes e inesperados. O primeiro episódio ocorreu no metrô: sentiu palpitação intensa, falta de ar, formigamento nas mãos, sudorese, e um medo avassalador de que ia morrer. O episódio durou cerca de 10 minutos e alcançou o pico em poucos minutos. Desde então, teve pelo menos mais 6 episódios semelhantes, em lugares variados (em casa, no trabalho, no supermercado), sem gatilho aparente. Evita usar o metrô desde o primeiro episódio. Vive em apreensão constante, com medo de quando será o próximo ataque. Tem ido ao pronto-socorro achando que está tendo infarto, mas os exames cardíacos dão normais. Mudou sua rotina — evita ir a lugares onde 'não possa sair rápido'. Nega uso de substâncias além de café pela manhã.",
        "transtorno": "panico",
        "criterios_key": "transtorno_de_panico",
        "diagnostico_real": "Transtorno de Pânico (F41.0)",
    },
    # 3. Ansiedade Social
    {
        "nome": "Beatriz",
        "idade": 22,
        "genero": "feminino",
        "ocupacao": "estudante de comunicação social",
        "estado_civil": "solteira, mora com os pais",
        "contexto": "Beatriz relata medo intenso de situações em que pode ser observada ou avaliada por outros. Sempre foi considerada 'timida', mas o quadro piorou significativamente ao entrar na faculdade há 3 anos. Tem pavor de apresentações de seminários — quando precisa apresentar, sente taquicardia, tremores, voz trêmula, rosto vermelho e sensação de que todos estão julgando. Evita comer na frente de colegas (só almoça se estiver sozinha ou com uma amiga próxima). Não vai a festas da faculdade. Recusou um estágio porque envolvia reuniões de equipe. Sente que é 'incompetente' e que os outros vão perceber. Chora com frequência pensando que não vai conseguir se formar. Nega uso de substâncias.",
        "transtorno": "ansiedade_social",
        "criterios_key": "transtorno_de_ansiedade_social",
        "diagnostico_real": "Transtorno de Ansiedade Social (F40.10)",
    },
    # 4. Fobia Específica
    {
        "nome": "Lucas",
        "idade": 35,
        "genero": "masculino",
        "ocupacao": "contador",
        "estado_civil": "casado, sem filhos",
        "contexto": "Lucas procura atendimento porque precisa fazer exames de sangue de rotina há mais de 2 anos e não consegue. Desde criança, tem medo intenso de sangue, agulhas e qualquer procedimento médico que envolva perfuração. Já desmaiou durante uma coleta de sangue aos 16 anos — sentiu tontura, náusea, visão escurecendo, e acordou no chão. Desde então, adia qualquer exame que envolva agulhas. Não consegue assistir cenas de filmes com sangue sem passar mal. A esposa está preocupada porque ele se recusa a ir ao médico. Até curativos com sangue o incomodam. Sabe que o medo é 'exagerado', mas não consegue controlar. A situação está afetando seu casamento e sua saúde. Nega qualquer outro medo intenso. Nega uso de substâncias.",
        "transtorno": "fobia_especifica",
        "criterios_key": "fobia_especifica",
        "diagnostico_real": "Fobia Específica — tipo sangue-injeção-ferimentos (F40.230)",
    },
    # 5. Agorafobia
    {
        "nome": "Helena",
        "idade": 40,
        "genero": "feminino",
        "ocupacao": "dona de casa",
        "estado_civil": "casada, três filhos adolescentes",
        "contexto": "Helena é trazida à consulta pelo marido. Nos últimos 2 anos, tem restringido progressivamente suas atividades fora de casa. Começou evitando ônibus e metrô — sentia pânico de ficar 'presa'. Depois parou de ir a supermercados lotados, evita filas, shopping centers e cinema. Há 6 meses, praticamente não sai de casa sozinha. Se precisa ir à padaria da esquina, liga para o marido ou um dos filhos para acompanhá-la. Se forçada a sair sozinha, sente falta de ar, coração acelerado, tontura e uma sensação de que algo terrível vai acontecer. O medo é de que não consiga 'escapar' ou que não tenha ajuda caso passe mal. Parou de ir às reuniões escolares dos filhos, não visita mais a mãe que mora em outro bairro. Sente-se 'prisioneira' em casa. Nega uso de substâncias.",
        "transtorno": "agorafobia",
        "criterios_key": "agorafobia",
        "diagnostico_real": "Agorafobia (F40.00)",
    },
    # 6. Ansiedade de Separação
    {
        "nome": "Rafael",
        "idade": 30,
        "genero": "masculino",
        "ocupacao": "analista financeiro",
        "estado_civil": "casado há 5 anos",
        "contexto": "Rafael procura atendimento por queixa de 'ansiedade que está atrapalhando o casamento'. Há 8 meses, quando sua esposa sofreu um acidente de carro (sem gravidade, apenas batida leve), Rafael passou a apresentar medo excessivo de se separar dela. Liga para a esposa de 6 a 8 vezes por dia para saber se está bem. Tem dificuldade extrema quando precisa viajar a trabalho — na última viagem, não conseguiu dormir e quase pegou um voo de volta no mesmo dia. Tem pesadelos recorrentes sobre a esposa sofrendo acidentes graves ou morrendo. Antes do acidente, já era 'um pouco preocupado' mas funcionava normalmente. Agora recusou uma promoção que exigiria viagens mensais. Quando a esposa sai à noite com amigas, fica inquieto, com taquicardia, e não consegue se concentrar em nada até ela voltar. Apresenta dor de estômago frequente nos dias em que sabe que vai se separar dela. Nega uso de substâncias.",
        "transtorno": "ansiedade_separacao",
        "criterios_key": "ansiedade_separacao",
        "diagnostico_real": "Transtorno de Ansiedade de Separação (F93.0)",
    },
    # 7. Mutismo Seletivo
    {
        "nome": "Sofia",
        "idade": 8,
        "genero": "feminino",
        "ocupacao": "estudante do 3º ano do ensino fundamental",
        "estado_civil": "criança, mora com os pais e um irmão de 5 anos",
        "contexto": "Sofia é trazida à consulta pela mãe, Dona Lúcia (38 anos, secretária). A mãe relata que Sofia 'não fala na escola' há cerca de 2 anos. Em casa, Sofia é comunicativa, brinca normalmente, conversa com os pais e o irmão, fala ao telefone com os avós. Porém, desde o 1º ano, não fala com professores, colegas nem funcionários da escola. Comunica-se na escola por gestos — aponta, acena com a cabeça, às vezes escreve bilhetes. As professoras já tentaram de tudo: incentivos, premiações, conversas individuais. Sofia simplesmente 'trava'. A mãe conta que em festas de aniversário de colegas, Sofia também não fala — fica perto da mãe e brinca sozinha. No consultório médico anterior, Sofia não falou uma palavra com o pediatra. A mãe está preocupada com o desempenho escolar e com a socialização. Nega problemas de linguagem ou audição. Sofia fala português fluentemente em casa, com vocabulário adequado para a idade.",
        "transtorno": "mutismo_seletivo",
        "criterios_key": "mutismo_seletivo",
        "diagnostico_real": "Mutismo Seletivo (F94.0)",
    },
    # 8. Ansiedade Induzida por Substância
    {
        "nome": "Jorge",
        "idade": 50,
        "genero": "masculino",
        "ocupacao": "empresário, dono de uma rede de cafeterias",
        "estado_civil": "casado, dois filhos adultos",
        "contexto": "Jorge procura atendimento por queixa de 'nervosismo e insônia que não passam'. Há cerca de 3 meses, vem apresentando inquietação constante, sensação de 'coração disparado', tremores finos nas mãos, dificuldade para dormir (demora horas para pegar no sono), e sensação de estar 'ligado no 220' o tempo todo. Relata que o negócio está passando por uma fase de expansão e atribui tudo ao 'estresse do trabalho'. O que Jorge NÃO menciona espontaneamente: consome de 8 a 10 xícaras de café por dia (expresso forte), começou a usar um descongestionante nasal com pseudoefedrina diariamente há 2 meses por uma sinusite persistente, e nos últimos meses aumentou significativamente o consumo de álcool social (3-4 doses de whisky quase toda noite 'para relaxar', com períodos de abstênêcia matinal que coincidem com piora da ansiedade). Ele SÓ revelará esses detalhes se o estudante perguntar DIRETAMENTE e ESPECIFICAMENTE sobre uso de cafeína, medicamentos de venda livre/nasal, e consumo de álcool. Se perguntado genericamente 'usa alguma substância?', dirá 'não, doutor, nada disso'. Somente com perguntas específicas revelará cada substância.",
        "transtorno": "ansiedade_substancia",
        "criterios_key": "ansiedade_substancia",
        "diagnostico_real": "Transtorno de Ansiedade Induzido por Substância/Medicamento (F15.980)",
    },
    # 9. Ansiedade Devida a Outra Condição Médica
    {
        "nome": "Dona Célia",
        "idade": 58,
        "genero": "feminino",
        "ocupacao": "aposentada, ex-funcionária pública",
        "estado_civil": "viúva há 3 anos, mora sozinha",
        "contexto": "Dona Célia procura atendimento por queixa de 'nervosismo e agitação que começaram do nada'. Há cerca de 4 meses, vem apresentando nervosismo intenso, sensação de coração acelerado (taquicardia), tremores nas mãos, perda de peso (emagreceu 6 kg sem fazer dieta), intolerância ao calor (sente muito calor mesmo em temperaturas amenas, sua excessivamente), insônia, e aumento do trânsito intestinal. Atribui tudo à viuvez e à solidão. O que Dona Célia NÃO sabe: seus sintomas são causados por hipertireoidismo não diagnosticado. Ela não fez exames de sangue há mais de 2 anos. Se o estudante perguntar sobre sintomas físicos, ela os descreverá naturalmente (calor, tremor, perda de peso, intestino solto, coração acelerado), mas ela NÃO associa esses sintomas a uma causa orgânica — acha que é 'ansiedade pela solidão'. O estudante precisa suspeitar de causa orgânica a partir do padrão de sintomas (taquicardia + perda de peso + intolerância ao calor + tremores) e mencionar a necessidade de exames laboratoriais (especialmente função tireoidiana). Nega uso de substâncias, não toma medicamentos.",
        "transtorno": "ansiedade_medica",
        "criterios_key": "ansiedade_medica",
        "diagnostico_real": "Transtorno de Ansiedade Devido a Outra Condição Médica — Hipertireoidismo (F06.4)",
    },
]

# ─── Mapping: transtorno key -> list of trackable criteria codes ─────────────────
CRITERIA_MAP = {
    "TAG": ["A", "B", "C1", "C2", "C3", "C4", "C5", "C6", "D", "E", "F"],
    "panico": ["A", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12", "A13", "B", "C", "D"],
    "ansiedade_social": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
    "fobia_especifica": ["A", "B", "C", "D", "E", "F", "G"],
    "agorafobia": ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
    "ansiedade_separacao": ["A", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "B", "C", "D"],
    "mutismo_seletivo": ["A", "B", "C", "D", "E"],
    "ansiedade_substancia": ["A", "B", "C", "D", "E", "EXAMES"],
    "ansiedade_medica": ["A", "B", "C", "D", "E", "EXAMES"],
}

# Core criteria used for score calculation per disorder
CORE_CRITERIA_MAP = {
    "TAG": {"A", "B", "C1", "C2", "C3", "C4", "C5", "C6", "D", "E"},
    "panico": {"A", "B", "C", "D"},
    "ansiedade_social": {"A", "B", "C", "D", "E", "F", "G", "H", "I"},
    "fobia_especifica": {"A", "B", "C", "D", "E", "F", "G"},
    "agorafobia": {"A", "B", "C", "D", "E", "F", "G", "H", "I"},
    "ansiedade_separacao": {"A", "B", "C", "D"},
    "mutismo_seletivo": {"A", "B", "C", "D", "E"},
    "ansiedade_substancia": {"A", "B", "C", "D", "E", "EXAMES"},
    "ansiedade_medica": {"A", "B", "C", "D", "E", "EXAMES"},
}


# ─── Dynamic System Prompt Builder ────────────────────────────────────────────
def build_system_prompt(profile: dict) -> str:
    now = datetime.now(ZoneInfo("America/Belem"))
    meses = [
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ]
    dias_semana = [
        "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
        "sexta-feira", "sábado", "domingo",
    ]
    data_formatada = f"{dias_semana[now.weekday()]}, {now.day} de {meses[now.month - 1]} de {now.year}"
    hora_formatada = f"{now.hour}:{now.minute:02d}"

    transtorno = profile["transtorno"]
    criterios_key = profile["criterios_key"]
    dsm_entry = DSM5_DATA["transtornos_de_ansiedade"][criterios_key]
    criterios_json = json.dumps(dsm_entry["criterios"], ensure_ascii=False, indent=2)

    # ── Base identity block ──────────────────────────────────────────
    identity_block = f"""## SUA IDENTIDADE
- Nome: {profile['nome']}
- Idade: {profile['idade']} anos
- Gênero: {profile['genero']}
- Ocupação: {profile['ocupacao']}
- Estado civil: {profile['estado_civil']}"""

    # ── Disorder-specific behaviour rules ────────────────────────────────
    disorder_rules = ""

    if transtorno == "mutismo_seletivo":
        disorder_rules = """
## DINÂMICA MÃE-CRIANÇA (REGRA ESPECIAL)
Você está simulando DUAS pessoas nesta consulta:
1. **Dona Lúcia** (mãe de Sofia, 38 anos, secretária) — que responde a maioria das perguntas do médico, descreve o comportamento da filha, fornece a história.
2. **Sofia** (8 anos) — que está presente na sala, mas NÃO fala com o médico.

### Regras de simulação:
- Quando o médico faz perguntas gerais ou se dirige à mãe, Dona Lúcia responde normalmente, em linguagem coloquial de mãe preocupada.
- Quando o médico tenta falar DIRETAMENTE com Sofia, descreva a reação dela em terceira pessoa entre colchetes, como: [Sofia olha para a mãe e não responde] ou [Sofia acena com a cabeça afirmativamente, mas não fala] ou [Sofia abaixa o olhar e se encolhe na cadeira].
- Sofia pode eventualmente dar respostas MUITO curtas sussurradas para a MÃE (nunca para o médico), como: [Sofia sussurra para a mãe: "sim"] — e a mãe repassa.
- Se o médico for especialmente gentil e paciente com Sofia, ela pode acenar ou apontar, mas não falar diretamente com ele.
- A mãe pode dizer coisas como "Vai, filha, fala pro doutor..." mas Sofia não fala.
- Dona Lúcia deve demonstrar frustração e preocupação materna ("Em casa ela fala pelos cotovelos, doutor, mas aqui ela trava...").

### Início da consulta:
Dona Lúcia cumprimenta o médico e diz algo como: "Boa tarde, doutor(a). Eu sou a Lúcia, mãe da Sofia. Viemos porque ela não fala na escola, e eu já não sei mais o que fazer..." [Sofia está sentada ao lado da mãe, olhando para o chão].
"""
    elif transtorno == "ansiedade_substancia":
        disorder_rules = """
## REGRAS ESPECIAIS PARA SUBSTÂNCIAS
- Você NÃO associa seus sintomas ao uso de substâncias. Você acha que está com 'estresse do trabalho'.
- Se o aluno perguntar genericamente sobre "drogas" ou "substâncias", responda: "Não, doutor, nada disso. Nunca usei droga na vida."
- CAFEÍNA: Só revele se perguntar ESPECIFICAMENTE sobre café ou cafeína. Revele então que toma 8-10 cafés por dia ("E que eu tenho cafeteria, doutor, o café é ali na mão o dia todo...").
- MEDICAMENTOS NASAIS: Só revele se perguntar ESPECIFICAMENTE sobre medicamentos de venda livre, spray nasal ou descongestionante. Revele então: "Ah, tem um spray nasal que uso faz uns 2 meses, pra sinusite. Comprei na farmácia, sem receita."
- ÁLCOOL: Se perguntar "bebe?", pode minimizar inicialmente ("socialmente, doutor"). Só revele a real frequência se o aluno insistir ou perguntar especificamente sobre quantidade e frequência. Revele então: "Olha, nos últimos meses tenho tomado uns whisky à noite pra relaxar... umas 3-4 doses quase toda noite."
- NÃO faça conexão entre as substâncias e seus sintomas. Essa é a descoberta que o aluno precisa fazer.
"""
    elif transtorno == "ansiedade_medica":
        disorder_rules = """
## REGRAS ESPECIAIS PARA CONDIÇÃO MÉDICA
- Você NÃO sabe que tem hipertireoidismo. Você acha que sua ansiedade é por causa da viuvez e solidão.
- Descreva os sintomas físicos NATURALMENTE quando perguntada (calor, tremor, perda de peso, intestino solto, taquicardia), mas NÃO os associe a uma doença orgânica. Diga coisas como: "Ando sentindo muito calor, mas acho que é da idade" ou "Emagreci, mas é porque perdi o apetite com a tristeza" ou "Meu coração dispara, deve ser dos nervos".
- Se o aluno perguntar sobre exames recentes, diga que faz mais de 2 anos que não faz check-up.
- Se o aluno mencionar tireoide ou pedir exames de sangue/TSH/T4, demonstre surpresa: "Tireoide, doutor(a)? Acha que pode ser isso? Nunca pensei nisso..."
- NÃO sugira espontaneamente a possibilidade de causa orgânica. Essa é a descoberta que o aluno precisa fazer.
"""

    # ── Build common behaviour rules ───────────────────────────────────
    common_rules = """
## REGRAS DE COMPORTAMENTO

1. **SEJA NATURAL**: Responda como um paciente real, com linguagem coloquial brasileira. NÃO use terminologia médica. Descreva seus sintomas com suas próprias palavras (ex: em vez de "fatigabilidade", diga "ando muito cansada, doutor, mesmo dormindo bastante não acordo descansada").

2. **RESPONDA APENAS AO QUE FOI PERGUNTADO**: Não ofereça informações espontaneamente. O aluno precisa inquirir adequadamente. Se perguntar "como você está?", dê uma resposta vaga como "não ando bem, doutor" e espere perguntas mais específicas.

3. **REVELE GRADUALMENTE**: Não despeje todos os sintomas de uma vez. Dê informações proporcionais à qualidade da pergunta. Perguntas abertas bem formuladas geram respostas mais ricas. Perguntas fechadas geram respostas curtas.

4. **NUNCA DÂ O DIAGNÓSTICO**: Você é paciente, não sabe o nome técnico do que tem. Diga coisas como "não sei o que tenho, por isso vim aqui", "acho que estou com algum problema dos nervos".

5. **DEMONSTRE EMOÇÕES REALISTAS**: Mostre preocupação, ansiedade ao falar de certos temas, alívio quando o médico demonstra empatia. Pode ficar um pouco reticente com perguntas muito diretas sobre saúde mental (estigma).

6. **MANTENHA COERÊNCIA**: Suas respostas devem ser consistentes ao longo da conversa. Não se contradiga.

7. **SOBRE PERGUNTAS DE RISCO (IDEAÇÃO SUICIDA)**: Se perguntado sobre pensamentos suicidas, responda que NÃO tem pensamentos suicidas ou de autolesão, mas que às vezes sente que "não aguenta mais" essa situação. Isso é importante para o aluno praticar a triagem de risco de forma segura.

8. **SOBRE SUBSTÂNCIAS E MEDICAMENTOS**: Responda conforme o contexto do perfil. Nega uso de drogas ilícitas.

9. **COMPRIMENTO DAS RESPOSTAS**: Mantenha respostas curtas a moderadas (2-5 frases), como um paciente real faria. Não faça monólogos longos a menos que provocado por uma pergunta muito aberta e empática.

10. **NUNCA SAIA DO PAPEL DE PACIENTE**: Mesmo que o aluno faça perguntas estranhas, tente dar diagnósticos, ou fuja do contexto clínico, você deve SEMPRE responder como paciente. Nunca dê feedback, avaliações ou orientações ao aluno durante a conversa. Você é APENAS o paciente.

11. **APARÊNCIA E VESTUÁRIO**: Na sua PRIMEIRA resposta, inclua entre asteriscos uma descrição detalhada da sua aparência ao entrar na sala: vestuário, higiene pessoal, postura corporal, expressão facial, objetos que carrega. Exemplo: *entra na sala vestindo calça jeans e camiseta, cabelo penteado, aparência cuidada, mãos inquietas no colo, expressão tensa*. Seja coerente com seu perfil e quadro clínico.

12. **EXPRESSÕES E GESTOS**: Ao longo de TODA a conversa, inclua sempre entre asteriscos descrições de comportamento não-verbal: mudanças de expressão facial, gestos, postura, contato visual, tom de voz, pausas, sinais de ansiedade (mexer mãos, evitar olhar, engolir seco, etc). Esses dados são essenciais para o Exame do Estado Mental. Exemplo: *desvia o olhar e mexe as mãos nervosamente* ou *faz uma pausa longa, engole seco*."""

    # ── Opening line rule (customized) ─────────────────────────────────
    if transtorno == "mutismo_seletivo":
        opening_rule = ""  # Opening is handled in disorder_rules
    else:
        opening_rule = f"""
13. **INÍCIO DA CONSULTA**: Na primeira mensagem (quando o aluno cumprimentar), apresente-se brevemente e diga algo como "{'Obrigada' if profile['genero'] == 'feminino' else 'Obrigado'} por me atender, doutor(a). Não tenho me sentido bem ultimamente..." e espere as perguntas."""

    # ── Assemble final prompt ───────────────────────────────────────────
    return f"""Você é um PACIENTE SIMULADO para treinamento de estudantes de Medicina em anamnese psiquiátrica.

## CONTEXTO TEMPORAL
Hoje é {data_formatada}, aproximadamente {hora_formatada} (horário de Belém). Use esta informação para responder perguntas sobre data, dia da semana, mês ou horário de forma coerente.

{identity_block}

## SEU QUADRO CLÍNICO (NUNCA REVELE DIRETAMENTE AO ALUNO)
Você apresenta: {dsm_entry['nome_completo']} ({dsm_entry['codigo_cid']}) conforme os critérios do DSM-5-TR.

Contexto da sua história:
{profile['contexto']}

## CRITÉRIOS DSM-5 DO SEU TRANSTORNO
{criterios_json}
{disorder_rules}
{common_rules}
{opening_rule}

LEMBRE-SE: Seu objetivo é treinar o aluno na coleta de dados e no raciocínio clínico. Seja um paciente realista e desafiador, mas cooperativo."""


# ─── Dynamic Tracker Prompt Builder ─────────────────────────────────────────
def build_tracker_prompt(profile: dict) -> str:
    transtorno = profile["transtorno"]
    criterios_key = profile["criterios_key"]
    dsm_entry = DSM5_DATA["transtornos_de_ansiedade"][criterios_key]
    nome_transtorno = dsm_entry["nome_completo"]
    codigo_cid = dsm_entry["codigo_cid"]

    # Build criteria list from the DSM data
    criteria_lines = []
    criterios = dsm_entry["criterios"]
    for code, value in criterios.items():
        if isinstance(value, str):
            criteria_lines.append(f"- {code}: {value}")
        elif isinstance(value, dict):
            desc = value.get("descricao", "")
            criteria_lines.append(f"- {code}: {desc}")
            # Include sub-items if present
            sintomas = value.get("sintomas", {})
            for sub_code, sub_val in sintomas.items():
                if isinstance(sub_val, str):
                    criteria_lines.append(f"  - {sub_code}: {sub_val}")
                elif isinstance(sub_val, dict):
                    criteria_lines.append(f"  - {sub_code}: {sub_val.get('descricao', '')}")
            # Include B1/B2 style sub-items
            for key in sorted(value.keys()):
                if key.startswith(code) and key != code and key not in sintomas:
                    sub_val = value[key]
                    if isinstance(sub_val, str):
                        criteria_lines.append(f"  - {key}: {sub_val}")

    # Add EXAMES for substance/medical disorders
    if transtorno in ["ansiedade_substancia", "ansiedade_medica"]:
        criteria_lines.append("  - EXAMES: Estudante identifica necessidade de exames complementares")

    criteria_text = "\n".join(criteria_lines)

    return f"""Você é um rastreador silencioso de critérios DSM-5-TR. Sua única função é analisar a conversa e identificar quais critérios clínicos foram abordados.

DIAGNÓSTICO DO PACIENTE: {nome_transtorno} ({codigo_cid})

CRITÉRIOS RASTREADOS:
{criteria_text}

INSTRUÇÕES:
1. Analise APENAS a última mensagem do estudante na conversa
2. Identifique quais critérios foram ABORDADOS (perguntados ou investigados) nessa mensagem
3. Retorne SOMENTE um JSON válido, sem texto adicional
4. Formato: {{"criterios_abordados": ["A", "C1", ...]}}
5. Use os códigos exatos dos critérios listados acima
6. Se nenhum critério foi abordado, retorne: {{"criterios_abordados": []}}
7. Para transtornos de substância/médicos, inclua "EXAMES" se o estudante solicitar exames complementares"""


# ─── In-Memory Session Store ──────────────────────────────────────────────────
SESSIONS: dict[str, dict] = {}


# ─── FastAPI Application ───────────────────────────────────────────────────────
app = FastAPI(title="PsiqMentor V5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic Models ──────────────────────────────────────────────────────────────
class StartRequest(BaseModel):
    patient_index: int | None = None


class ChatRequest(BaseModel):
    session_id: str
    message: str
    student_name: str = ""
    student_id: str = ""


class AudioRequest(BaseModel):
    session_id: str
    text: str


# ─── Helper: parse patient response into (behavior_text, spoken_text) ─────────
def parse_patient_response(raw: str) -> tuple[str, str]:
    """Separates *behavioral descriptions* from spoken dialogue.

    Behavioral text is enclosed in *asterisks*. Everything else is spoken.
    Returns (behavior_html, spoken_text).
    """
    # Extract behavior parts
    behavior_parts = re.findall(r"\*([^*]+)\*", raw)
    behavior_html = " ".join(f"<em>{p.strip()}</em>" for p in behavior_parts) if behavior_parts else ""

    # Remove behavior from spoken text
    spoken = re.sub(r"\*[^*]+\*", "", raw).strip()
    # Clean up extra whitespace/newlines
    spoken = re.sub(r"\n+", " ", spoken)
    spoken = re.sub(r"  +", " ", spoken).strip()

    return behavior_html, spoken


# ─── Routes ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the frontend HTML."""
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/api/start")
async def start_session(req: StartRequest):
    """Create a new simulation session."""
    if req.patient_index is not None:
        idx = req.patient_index % len(PATIENT_PROFILES)
    else:
        idx = random.randint(0, len(PATIENT_PROFILES) - 1)

    profile = PATIENT_PROFILES[idx]
    session_id = str(uuid.uuid4())

    SESSIONS[session_id] = {
        "profile": profile,
        "messages": [],
        "criteria_hit": set(),
        "start_time": time.time(),
        "student_name": "",
        "student_id": "",
    }
    return {"session_id": session_id, "patient_name": profile["nome"], "patient_index": idx}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Send a message and get patient response."""
    session = SESSIONS.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Store student identification if provided
    if req.student_name:
        session["student_name"] = req.student_name
    if req.student_id:
        session["student_id"] = req.student_id

    profile = session["profile"]
    messages = session["messages"]

    # Add user message
    messages.append({"role": "user", "content": req.message})

    # Get patient response
    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=build_system_prompt(profile),
        messages=messages,
    )
    raw_response = response.content[0].text
    messages.append({"role": "assistant", "content": raw_response})

    # Async tracker call (fire-and-forget)
    asyncio.create_task(_track_criteria(session, req.message))

    # Parse response
    behavior_html, spoken_text = parse_patient_response(raw_response)

    return {
        "response": raw_response,
        "behavior_html": behavior_html,
        "spoken_text": spoken_text,
    }


async def _track_criteria(session: dict, student_message: str) -> None:
    """Background task: track DSM-5 criteria coverage."""
    profile = session["profile"]
    messages = session["messages"]

    client = Anthropic()
    try:
        tracker_response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            system=build_tracker_prompt(profile),
            messages=[
                {
                    "role": "user",
                    "content": f"Conversa até agora:\n{json.dumps(messages, ensure_ascii=False)}\n\nÚltima mensagem do estudante: {student_message}",
                }
            ],
        )
        result = json.loads(tracker_response.content[0].text)
        new_criteria = result.get("criterios_abordados", [])
        session["criteria_hit"].update(new_criteria)
    except Exception:
        pass  # Silent failure


@app.post("/api/audio")
async def generate_tts(req: AudioRequest):
    """Generate TTS audio for a patient response."""
    session = SESSIONS.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    profile = session["profile"]
    transtorno = profile["transtorno"]

    # Map disorder to voice
    VOICE_MAP = {
        "TAG": "kore",            # Warm, slightly anxious female
        "panico": "charon",       # Tense male
        "ansiedade_social": "zephyr",  # Soft, hesitant female
        "fobia_especifica": "fenrir",  # Nervous male
        "agorafobia": "aoede",    # Fearful female
        "ansiedade_separacao": "orus",  # Worried male
        "mutismo_seletivo": "kore",    # Mother's voice (Sofia doesn't speak)
        "ansiedade_substancia": "puck",  # Middle-aged male
        "ansiedade_medica": "schedar",   # Older female
    }
    voice = VOICE_MAP.get(transtorno, "kore")

    try:
        audio_bytes = await generate_audio(req.text, voice=voice)
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=response.mp3"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS error: {e}")


@app.get("/api/evaluate/{session_id}")
async def evaluate_session(session_id: str, student_name: str = Query(default=""), student_id: str = Query(default="")):
    """Evaluate the anamnesis quality and generate a formative report."""
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    profile = session["profile"]
    messages = session["messages"]
    criteria_hit = session["criteria_hit"]

    # Update student info if provided in query params
    if student_name:
        session["student_name"] = student_name
    if student_id:
        session["student_id"] = student_id

    # Calculate criteria coverage
    transtorno = profile["transtorno"]
    core_criteria = CORE_CRITERIA_MAP.get(transtorno, set())
    covered = criteria_hit & core_criteria
    coverage_pct = len(covered) / len(core_criteria) * 100 if core_criteria else 0

    # Build conversation transcript for evaluation
    transcript = ""
    for msg in messages:
        role = "Estudante" if msg["role"] == "user" else "Paciente"
        transcript += f"{role}: {msg['content']}\n\n"

    # Build evaluation prompt
    eval_prompt = f"""Você é um professor de semiologia psiquiátrica avaliando a qualidade de uma anamnese realizada por um estudante de medicina.

PACIENTE SIMULADO: {profile['nome']}, {profile['idade']} anos
DIAGNÓSTICO REAL: {profile['diagnostico_real']}
CRITÉRIOS ABORDADOS: {', '.join(sorted(criteria_hit)) if criteria_hit else 'Nenhum'}
COBERTURA: {coverage_pct:.0f}% dos critérios nucleares

TRANSCRIÇÃO DA ENTREVISTA:
{transcript}

Avalie a anamnese em 6 dimensões (nota de 0-10 cada):
1. Abertura e rapport
2. Técnica de questionamento (perguntas abertas vs fechadas)
3. Cobertura dos sintomas principais
4. Investigação de critérios diagnósticos DSM-5
5. Avaliação do impacto funcional
6. Triagem de risco (ideação suicida, uso de substâncias)

Formato de resposta (JSON):
{{
    "dimensoes": {{
        "abertura_rapport": {{"nota": X, "feedback": "..."}},
        "tecnica_questionamento": {{"nota": X, "feedback": "..."}},
        "cobertura_sintomas": {{"nota": X, "feedback": "..."}},
        "criterios_dsm5": {{"nota": X, "feedback": "..."}},
        "impacto_funcional": {{"nota": X, "feedback": "..."}},
        "triagem_risco": {{"nota": X, "feedback": "..."}}
    }},
    "nota_geral": X.X,
    "pontos_fortes": ["...", "..."],
    "areas_melhoria": ["...", "..."],
    "diagnostico_sugerido": "...",
    "diagnostico_correto": "{profile['diagnostico_real']}",
    "acertou_diagnostico": true/false,
    "resumo_formativo": "Parágrafo com orientações pedagógicas construtivas..."
}}"""

    client = Anthropic()
    eval_response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": eval_prompt}],
    )

    try:
        eval_data = json.loads(eval_response.content[0].text)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        text = eval_response.content[0].text
        start = text.find("{")
        end = text.rfind("}") + 1
        eval_data = json.loads(text[start:end])

    return {
        "evaluation": eval_data,
        "criteria_hit": list(criteria_hit),
        "coverage_pct": coverage_pct,
        "duration_minutes": (time.time() - session["start_time"]) / 60,
        "student_name": session.get("student_name", ""),
        "student_id": session.get("student_id", ""),
    }


@app.get("/api/export/{session_id}")
async def export_session(session_id: str):
    """Export session data as CSV for longitudinal tracking."""
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    profile = session["profile"]
    criteria_hit = session["criteria_hit"]
    transtorno = profile["transtorno"]
    core_criteria = CORE_CRITERIA_MAP.get(transtorno, set())
    covered = criteria_hit & core_criteria
    coverage_pct = len(covered) / len(core_criteria) * 100 if core_criteria else 0

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["session_id", "patient", "disorder", "criteria_hit", "coverage_pct", "duration_min"])
    writer.writerow([
        session_id,
        profile["nome"],
        profile["diagnostico_real"],
        "|".join(sorted(criteria_hit)),
        f"{coverage_pct:.1f}",
        f"{(time.time() - session['start_time']) / 60:.1f}",
    ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=session_{session_id[:8]}.csv"},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(SESSIONS)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
