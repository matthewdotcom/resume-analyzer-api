"""
AI Resume Analyzer API

Production-ready FastAPI service that compares a resume against a job
description and returns a match score, strengths, missing keywords and a
summary verdict. The analysis engine is a mock AI (keyword-based NLP) that
can be swapped for a real LLM call without changing the API contract.

Run:
    uvicorn main:app --reload
Docs:
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
APP_NAME = 'AI Resume Analyzer API'
APP_VERSION = '1.0.0'
ALLOWED_ORIGINS = [o.strip() for o in os.getenv('ALLOWED_ORIGINS', '*').split(',') if o.strip()]
SIMULATED_LATENCY = float(os.getenv('SIMULATED_LATENCY', '0.4'))

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'),
                    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s')
logger = logging.getLogger('resume_analyzer')

APP_DESCRIPTION = '''
Analyze how well a **resume** matches a **job description** in milliseconds.

### Features
- 🎯 **Match score** from 0 to 100
- 💪 **Strengths** - skills and keywords the candidate already covers
- 🔍 **Missing keywords** - gaps to address before applying
- 📝 **Summary verdict** - a plain-language recommendation

> The current engine is a mock AI built on keyword analysis. It can be replaced
> with a real LLM provider without changing the request or response schema.
'''

TAGS_METADATA = [
    {'name': 'Resume Analysis', 'description': 'Score a resume against a job description.'},
    {'name': 'Health', 'description': 'Service health and uptime checks.'},
]

# ---------------------------------------------------------------------------
# Keyword knowledge base (mock AI)
# ---------------------------------------------------------------------------
KNOWN_SKILLS = {
    'python', 'java', 'javascript', 'typescript', 'go', 'rust', 'c++', 'c#', 'php', 'ruby',
    'react', 'vue', 'angular', 'next.js', 'node.js', 'fastapi', 'django', 'flask', 'express',
    'sql', 'postgresql', 'mysql', 'mongodb', 'redis', 'graphql', 'rest', 'api',
    'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'terraform', 'linux', 'git', 'github', 'ci/cd',
    'pandas', 'numpy', 'machine learning', 'deep learning', 'nlp', 'data analysis', 'tensorflow',
    'pytorch', 'excel', 'tableau', 'power bi', 'html', 'css', 'tailwind', 'figma',
    'agile', 'scrum', 'communication', 'leadership', 'teamwork', 'problem solving',
    'project management', 'seo', 'marketing', 'sales', 'testing', 'microservices',
}

DISPLAY_NAMES = {
    'javascript': 'JavaScript', 'typescript': 'TypeScript', 'node.js': 'Node.js',
    'next.js': 'Next.js', 'fastapi': 'FastAPI', 'postgresql': 'PostgreSQL', 'mysql': 'MySQL',
    'mongodb': 'MongoDB', 'graphql': 'GraphQL', 'github': 'GitHub', 'ci/cd': 'CI/CD',
    'numpy': 'NumPy', 'pytorch': 'PyTorch', 'tensorflow': 'TensorFlow', 'power bi': 'Power BI',
    'git': 'Git', 'go': 'Go', 'rest': 'REST', 'nlp': 'NLP', 'seo': 'SEO',
}

STOPWORDS = {
    'the', 'and', 'for', 'with', 'you', 'your', 'our', 'are', 'will', 'this', 'that', 'from',
    'have', 'has', 'into', 'who', 'what', 'about', 'their', 'they', 'them', 'all', 'any', 'can',
    'such', 'must', 'should', 'would', 'able', 'also', 'more', 'other', 'than', 'well', 'work',
    'working', 'role', 'team', 'join', 'looking', 'candidate', 'ideal', 'strong', 'years',
    'year', 'experience', 'experienced', 'knowledge', 'skills', 'ability', 'including',
    'within', 'across', 'using', 'used', 'based', 'plus', 'preferred', 'required',
    'requirements', 'responsibilities', 'company', 'help', 'build', 'building', 'new',
    'good', 'great', 'excellent', 'etc', 'like', 'per', 'via', 'not', 'but', 'its',
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ResumeAnalysisRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            'examples': [{
                'resume_text': (
                    'Backend developer with 4 years of experience building REST APIs in '
                    'Python using FastAPI and Django. Skilled in PostgreSQL, Docker and Git. '
                    'Led a team of three engineers in an Agile environment and improved API '
                    'response times by 40 percent.'
                ),
                'job_description': (
                    'We are hiring a Backend Engineer with strong Python and FastAPI skills. '
                    'Experience with PostgreSQL, Docker, Kubernetes and AWS is required. '
                    'Familiarity with CI/CD pipelines, Redis and microservices is a plus.'
                ),
            }]
        },
    )

    resume_text: str = Field(
        ..., min_length=50, max_length=20_000,
        description='Full plain-text content of the candidate resume.',
    )
    job_description: str = Field(
        ..., min_length=30, max_length=10_000,
        description='Full plain-text job description to compare against.',
    )


class ResumeAnalysisResponse(BaseModel):
    match_score: int = Field(..., ge=0, le=100, description='Overall match score from 0 to 100.')
    strengths: list[str] = Field(..., description='Areas where the resume matches the role.')
    missing_keywords: list[str] = Field(..., description='Important job keywords not found in the resume.')
    summary_verdict: str = Field(..., description='Plain-language recommendation.')

    model_config = ConfigDict(json_schema_extra={
        'examples': [{
            'match_score': 63,
            'strengths': [
                'Demonstrated Docker experience',
                'Demonstrated FastAPI experience',
                'Demonstrated PostgreSQL experience',
                'Demonstrated Python experience',
            ],
            'missing_keywords': ['AWS', 'Kubernetes', 'CI/CD', 'Microservices', 'Redis'],
            'summary_verdict': 'Good match. The candidate covers most core requirements; '
                               'adding AWS, Kubernetes would strengthen the application.',
        }]
    })


class HealthResponse(BaseModel):
    status: str = Field(..., examples=['ok'])
    version: str = Field(..., examples=[APP_VERSION])
    timestamp: datetime


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Mock AI engine
# ---------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    tokens = re.findall(r'[a-z][a-z0-9+#./-]*', text.lower())
    return [t.rstrip('.,/-') for t in tokens]


def contains_term(term: str, padded_text: str) -> bool:
    return f' {term} ' in padded_text


def display_name(term: str) -> str:
    if term in DISPLAY_NAMES:
        return DISPLAY_NAMES[term]
    return term.upper() if len(term) <= 3 else term.title()


def extract_job_keywords(job_description: str, limit: int = 20) -> list[str]:
    """Known skills first, then the most frequent meaningful terms."""
    tokens = tokenize(job_description)
    padded = ' ' + ' '.join(tokens) + ' '

    keywords = [skill for skill in sorted(KNOWN_SKILLS) if contains_term(skill, padded)]
    frequent = Counter(t for t in tokens if len(t) >= 4 and t not in STOPWORDS and t not in KNOWN_SKILLS)
    for term, count in frequent.most_common():
        if len(keywords) >= limit:
            break
        if count >= 2:
            keywords.append(term)
    return keywords[:limit]


NICE_TO_HAVE_MARKERS = (
    'a plus', 'nice to have', 'bonus', 'preferred', 'familiarity', 'familiar with',
    'optional', 'advantage', 'desirable',
)


def split_sentences(text: str) -> list[str]:
    """Split on sentence punctuation without breaking terms like Node.js."""
    parts = re.split(r'[;!?]+|[.](?= |$)', text.replace('\n', ' . '))
    return [p.strip().lower() for p in parts if p.strip()]


def find_optional_keywords(job_description: str, keywords: list[str]) -> set[str]:
    """Keywords that only appear in nice-to-have sentences (e.g. 'X is a plus')."""
    sentences = [(s, ' ' + ' '.join(tokenize(s)) + ' ') for s in split_sentences(job_description)]
    optional = set()
    for keyword in keywords:
        hits = [raw for raw, padded in sentences if contains_term(keyword, padded)]
        if hits and all(any(m in raw for m in NICE_TO_HAVE_MARKERS) for raw in hits):
            optional.add(keyword)
    return optional


def build_verdict(score: int, missing_required: list[str], missing_optional: list[str]) -> str:
    gap_list = missing_required or missing_optional
    gaps = ', '.join(display_name(k) for k in gap_list[:3])
    if score >= 80:
        return 'Excellent match. The resume aligns strongly with the role and is ready to submit.'
    if score >= 60:
        return ('Good match. The candidate covers most core requirements'
                + (f'; adding {gaps} would strengthen the application.' if gaps else '.'))
    if score >= 40:
        return ('Partial match. Relevant experience is present, but key requirements'
                + (f' such as {gaps}' if gaps else '') + ' are missing.')
    return 'Weak match. The resume does not reflect most of the core requirements for this role.'


async def mock_ai_analyze(resume_text: str, job_description: str) -> ResumeAnalysisResponse:
    """Simulate an AI model analyzing resume fit. Swap with a real LLM call later."""
    if SIMULATED_LATENCY > 0:
        await asyncio.sleep(random.uniform(SIMULATED_LATENCY * 0.5, SIMULATED_LATENCY))

    job_keywords = extract_job_keywords(job_description)
    optional = find_optional_keywords(job_description, job_keywords)
    resume_tokens = tokenize(resume_text)
    resume_padded = ' ' + ' '.join(resume_tokens) + ' '

    def weight(keyword: str) -> float:
        # Known skills count double; nice-to-have keywords count half.
        base = 2.0 if keyword in KNOWN_SKILLS else 1.0
        return base * 0.5 if keyword in optional else base

    matched = [k for k in job_keywords if contains_term(k, resume_padded)]
    missing = [k for k in job_keywords if k not in matched]
    missing_required = [k for k in missing if k not in optional]
    missing_optional = [k for k in missing if k in optional]

    total_weight = sum(weight(k) for k in job_keywords) or 1.0
    coverage = sum(weight(k) for k in matched) / total_weight

    # Soft curve rewards partial coverage; short resumes get a mild penalty only.
    word_count = len(resume_tokens)
    detail = min(word_count / 80, 1.0)
    score = max(0, min(100, round(100 * coverage ** 0.6 * (0.85 + 0.15 * detail))))

    ranked = sorted(matched, key=weight, reverse=True)
    strengths = [f'Demonstrated {display_name(k)} experience' for k in ranked[:6]]
    if word_count >= 80:
        strengths.append('Resume provides a solid level of detail')
    if not strengths:
        strengths.append('Resume submitted in a readable plain-text format')

    return ResumeAnalysisResponse(
        match_score=score,
        strengths=strengths,
        missing_keywords=[display_name(k) for k in (missing_required + missing_optional)[:10]],
        summary_verdict=build_verdict(score, missing_required, missing_optional),
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    application = FastAPI(
        title=APP_NAME,
        description=APP_DESCRIPTION,
        version=APP_VERSION,
        openapi_tags=TAGS_METADATA,
        docs_url='/docs',
        redoc_url='/redoc',
        openapi_url='/api/v1/openapi.json',
        contact={'name': 'Matthew', 'url': 'https://github.com/matthewdotcom'},
        license_info={'name': 'MIT', 'url': 'https://opensource.org/licenses/MIT'},
        swagger_ui_parameters={
            'displayRequestDuration': True,
            'tryItOutEnabled': True,
            'defaultModelsExpandDepth': 1,
            'syntaxHighlight.theme': 'monokai',
        },
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=ALLOWED_ORIGINS != ['*'],
        allow_methods=['GET', 'POST', 'OPTIONS'],
        allow_headers=['*'],
    )

    @application.middleware('http')
    async def add_process_time_header(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers['X-Process-Time-Ms'] = f'{elapsed_ms:.1f}'
        logger.info('%s %s -> %d (%.1f ms)', request.method, request.url.path,
                    response.status_code, elapsed_ms)
        return response

    application.include_router(system_router)
    application.include_router(api_router)
    return application


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
system_router = APIRouter()
api_router = APIRouter(prefix='/api/v1', tags=['Resume Analysis'])


@system_router.get('/', include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url='/docs')


@system_router.get('/health', response_model=HealthResponse, tags=['Health'],
                   summary='Health check')
async def health() -> HealthResponse:
    return HealthResponse(status='ok', version=APP_VERSION,
                          timestamp=datetime.now(timezone.utc))


@api_router.post(
    '/analyze-resume',
    response_model=ResumeAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary='Analyze a resume against a job description',
    description=(
        'Compares the resume text with the job description and returns a match score '
        '(0-100), strengths, missing keywords and a summary verdict.'
    ),
    response_description='Resume analysis result',
    responses={
        422: {'description': 'Validation error - e.g. text too short or missing fields'},
        500: {'model': ErrorResponse, 'description': 'Unexpected analysis failure'},
    },
)
async def analyze_resume(payload: ResumeAnalysisRequest) -> ResumeAnalysisResponse:
    try:
        return await mock_ai_analyze(payload.resume_text, payload.job_description)
    except Exception as exc:
        logger.exception('Resume analysis failed')
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Analysis failed. Please try again later.',
        ) from exc


app = create_app()


if __name__ == '__main__':
    import uvicorn

    uvicorn.run('main:app', host='0.0.0.0', port=int(os.getenv('PORT', '8000')))
