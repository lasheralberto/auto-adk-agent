import numpy as np
import gymnasium as gym
from gymnasium import spaces
import json
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse
import warnings

# ──────────────────────────────────────────────
# 1.  STRAVA DATA FETCHER
# ──────────────────────────────────────────────



# ──────────────────────────────────────────────
# 2.  CYCLING RL ENVIRONMENT
# ──────────────────────────────────────────────

class CyclingEnv(gym.Env):
    """
    Entorno Gymnasium para optimizar la planificación del entrenamiento ciclista.

    ┌─────────────────────────────────────────────────────────────────┐
    │  STATE SPACE  (8 dimensiones, todas normalizadas [0, 1])        │
    │  ─────────────────────────────────────────────────────────────  │
    │  0  CTL (forma física)         [0 – 150 TSS/día]               │
    │  1  ATL (fatiga aguda)         [0 – 150 TSS/día]               │
    │  2  TSB (frescura)             [-60 – +60]                      │
    │  3  Días de descanso           [0 – 14 días]                    │
    │  4  W/kg medios 7d             [0 – 6 w/kg]                     │
    │  5  FC media 7d                [50 – 200 bpm]                   │
    │  6  FTP estimado               [100 – 500 W]                   │
    │  7  Día de la semana           [0 – 6]                          │
    └─────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────────┐
    │  ACTION SPACE  (5 acciones discretas)                           │
    │  ─────────────────────────────────────────────────────────────  │
    │  0  DESCANSO          → TSS objetivo = 0                        │
    │  1  RODAJE SUAVE Z1   → 50-60% FTP  |  55-65% FC máx           │
    │  2  FONDO Z2          → 60-75% FTP  |  65-75% FC máx           │
    │  3  UMBRAL Z3-Z4      → 75-95% FTP  |  75-90% FC máx           │
    │  4  VO2MAX Z5         → 95-120% FTP |  90-100% FC máx          │
    └─────────────────────────────────────────────────────────────────┘

    REWARD:
        + Aumento de CTL (ganancias de forma) escalado
        - Penalización por sobreentrenamiento (ATL muy alta sin base CTL)
        - Penalización por exceso de descanso (pérdida de forma)
        + Bonus por seguir principios de periodización (carga → recuperación)
    """

    metadata = {"render_modes": ["human", "ansi"]}

    # Rangos para normalización
    _CTL_MAX    = 150.0
    _ATL_MAX    = 150.0
    _TSB_MIN    = -60.0;  _TSB_MAX = 60.0
    _REST_MAX   = 14.0
    _WPKG_MAX   = 6.0
    _HR_MIN     = 50.0;   _HR_MAX  = 200.0
    _FTP_MIN    = 100.0;  _FTP_MAX = 500.0

    # TSS objetivo por acción (en un entrenamiento de ~1h)
    _ACTION_TSS = {
        0: 0,     # Descanso
        1: 30,    # Rodaje suave Z1
        2: 55,    # Fondo Z2
        3: 85,    # Umbral Z3-Z4
        4: 110,   # VO2max Z5
    }

    _ACTION_NAMES = {
        0: "🛌 DESCANSO",
        1: "🚴 RODAJE SUAVE Z1 (~55% FTP)",
        2: "🚴 FONDO Z2 (~68% FTP)",
        3: "⚡ UMBRAL Z3-Z4 (~85% FTP)",
        4: "🔥 VO2MAX Z5 (>95% FTP)",
    }

    def __init__(
        self,
        strava_data: Optional[dict] = None,
        episode_days: int = 28,
        render_mode: Optional[str] = None,
    ):
        """
        Parámetros
        ----------
        strava_data  : dict devuelto por get_strava_data(). Si es None,
                       se inicializa con valores sintéticos para pruebas.
        episode_days : duración de cada episodio en días (default 28 = 4 semanas).
        render_mode  : 'human' para imprimir en consola, None para silencioso.
        """
        super().__init__()
        self.render_mode  = render_mode
        self.episode_days = episode_days

        # ── Cargar datos Strava o usar sintéticos ────────────────────────
        if strava_data is not None:
            self._init_from_strava(strava_data)
        else:
            warnings.warn("No se proporcionaron datos Strava. Usando valores sintéticos.")
            self._init_synthetic()

        # ── Espacios ─────────────────────────────────────────────────────
        self.observation_space = spaces.Box(
            low   = np.zeros(8, dtype=np.float32),
            high  = np.ones(8,  dtype=np.float32),
            dtype = np.float32,
        )
        self.action_space = spaces.Discrete(5)

        # Estado interno
        self._day       = 0
        self._ctl       = self._init_ctl
        self._atl       = self._init_atl
        self._rest_days = self._init_rest_days
        self._ftp       = self._init_ftp
        self._weight_kg = self._init_weight
        self._hr_7d     = self._init_hr_7d
        self._tss_history: list[float] = []

    # ── helpers de inicialización ────────────────────────────────────────

    def _init_from_strava(self, data: dict):
        self._init_ctl        = float(data["ctl"])
        self._init_atl        = float(data["atl"])
        self._init_rest_days  = int(data["rest_days"])
        self._init_ftp        = float(data["ftp_estimate"])
        self._init_weight     = float(data["weight_kg"])
        self._init_hr_7d      = float(data["avg_hr_7d"]) if data["avg_hr_7d"] > 0 else 140.0
        self._init_wpkg_7d    = float(data["wpkg_7d"])

    def _init_synthetic(self):
        """Valores de un ciclista amateur de nivel medio para pruebas."""
        self._init_ctl        = 55.0
        self._init_atl        = 45.0
        self._init_rest_days  = 1
        self._init_ftp        = 230.0
        self._init_weight     = 72.0
        self._init_hr_7d      = 148.0
        self._init_wpkg_7d    = 2.9

    # ── Gymnasium API ────────────────────────────────────────────────────

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._day        = 0
        self._ctl        = self._init_ctl
        self._atl        = self._init_atl
        self._rest_days  = self._init_rest_days
        self._ftp        = self._init_ftp
        self._hr_7d      = self._init_hr_7d
        self._tss_history = []

        obs  = self._get_obs()
        info = self._get_info()
        if self.render_mode == "human":
            self._render_human("RESET", None, 0, obs)
        return obs, info

    def step(self, action: int):
        assert self.action_space.contains(action), f"Acción inválida: {action}"

        tss_applied = self._ACTION_TSS[action]

        # ── Actualizar CTL y ATL (PMC) ───────────────────────────────────
        prev_ctl     = self._ctl
        k_ctl        = 1 - np.exp(-1 / 42)
        k_atl        = 1 - np.exp(-1 / 7)
        self._ctl    = self._ctl + k_ctl * (tss_applied - self._ctl)
        self._atl    = self._atl + k_atl * (tss_applied - self._atl)
        tsb          = self._ctl - self._atl

        # ── Actualizar descanso ──────────────────────────────────────────
        if tss_applied == 0:
            self._rest_days += 1
        else:
            self._rest_days  = 0

        # ── Actualizar HR simulada (simplificado) ────────────────────────
        hr_targets = {0: 60, 1: 130, 2: 145, 3: 160, 4: 175}
        self._hr_7d = 0.85 * self._hr_7d + 0.15 * hr_targets[action]

        self._tss_history.append(tss_applied)
        self._day += 1

        # ── REWARD ───────────────────────────────────────────────────────
        reward = self._compute_reward(
            action      = action,
            prev_ctl    = prev_ctl,
            tss_applied = tss_applied,
            tsb         = tsb,
        )

        # ── Fin de episodio ──────────────────────────────────────────────
        terminated = self._day >= self.episode_days
        truncated  = False

        obs  = self._get_obs()
        info = self._get_info()

        if self.render_mode == "human":
            self._render_human(self._ACTION_NAMES[action], action, reward, obs)

        return obs, reward, terminated, truncated, info

    # ── Reward function ──────────────────────────────────────────────────

    def _compute_reward(self, action, prev_ctl, tss_applied, tsb) -> float:
        """
        Función de recompensa basada en principios de periodización:

        1. Ganancia de CTL       → queremos que suba pero gradualmente
        2. Penalizar sobreentrenamiento (ATL >> CTL y muchos días duros seguidos)
        3. Penalizar exceso de descanso (CTL cae, nos desentrenamos)
        4. Bonus por buena gestión del TSB (positivo en carrera, negativo en bloque)
        5. Penalizar incoherencia (VO2max con mucha fatiga, descanso con mucho TSB positivo)
        """
        reward = 0.0

        ctl_gain = self._ctl - prev_ctl
        atl       = self._atl

        # 1. Premio por ganancia de CTL (fitness acumulado)
        reward += ctl_gain * 3.0

        # 2. Penalización por sobreentrenamiento
        #    Si ATL > CTL + 20, estamos acumulando más fatiga de la que el cuerpo asimila
        overtraining_penalty = max(0, atl - self._ctl - 20) * 0.5
        reward -= overtraining_penalty

        # 3. Penalización por exceso de descanso
        if self._rest_days > 3:
            reward -= (self._rest_days - 3) * 1.5

        # 4. Bonus por TSB adecuado según la acción
        #    Entrenar duro con TSB positivo es mejor; descansar con TSB negativo es correcto
        if action in (3, 4) and tsb > 5:
            reward += 2.0   # buena frescura para intensidad
        if action in (3, 4) and tsb < -15:
            reward -= 3.0   # intentar intensidad muy fatigado → penalización
        if action == 0 and tsb < -10:
            reward += 1.5   # descanso necesario y bien tomado

        # 5. W/kg indirecto: mantener FTP estable o subir es positivo
        #    (simplificado: CTL alta con acciones de calidad sube el FTP estimado)
        if self._ctl > prev_ctl and action in (2, 3):
            reward += 0.5

        return float(reward)

    # ── Observación normalizada ──────────────────────────────────────────

    def _get_obs(self) -> np.ndarray:
        tsb    = self._ctl - self._atl
        wpkg   = (self._ctl / self._weight_kg) * 0.05   # proxy simplificado

        obs = np.array([
            np.clip(self._ctl / self._CTL_MAX, 0, 1),
            np.clip(self._atl / self._ATL_MAX, 0, 1),
            np.clip((tsb - self._TSB_MIN) / (self._TSB_MAX - self._TSB_MIN), 0, 1),
            np.clip(self._rest_days / self._REST_MAX, 0, 1),
            np.clip(wpkg / self._WPKG_MAX, 0, 1),
            np.clip((self._hr_7d - self._HR_MIN) / (self._HR_MAX - self._HR_MIN), 0, 1),
            np.clip((self._ftp - self._FTP_MIN) / (self._FTP_MAX - self._FTP_MIN), 0, 1),
            (self._day % 7) / 6.0,   # día de la semana normalizado
        ], dtype=np.float32)
        return obs

    def _get_info(self) -> dict:
        return {
            "day":        self._day,
            "ctl":        round(self._ctl, 2),
            "atl":        round(self._atl, 2),
            "tsb":        round(self._ctl - self._atl, 2),
            "rest_days":  self._rest_days,
            "ftp":        round(self._ftp, 1),
            "hr_7d":      round(self._hr_7d, 1),
        }

    # ── Render ───────────────────────────────────────────────────────────

    def _render_human(self, action_name, action, reward, obs):
        tsb = self._ctl - self._atl
        print(
            f"Día {self._day:>3} │ {action_name:<30} │ "
            f"CTL={self._ctl:5.1f}  ATL={self._atl:5.1f}  TSB={tsb:+5.1f} │ "
            f"reward={reward:+6.2f}"
        )

    def render(self):
        if self.render_mode == "human":
            info = self._get_info()
            print("\n── Estado actual del entorno ──────────────────────────────")
            for k, v in info.items():
                print(f"  {k:12}: {v}")
            print("───────────────────────────────────────────────────────────\n")



class StravaData:
    @staticmethod
    def build_authorization_url(
        client_id: int,
        redirect_uri: str,
        scope: str,
        state: Optional[str] = None,
        approval_prompt: Optional[str] = 'force',
    ) -> str:
        if not isinstance(redirect_uri, str):
            raise ValueError('redirect_uri must be a string')
        if not isinstance(scope, str):
            raise ValueError('scope must be a string')

        redirect_uri = redirect_uri.strip()
        scope = scope.strip()
        if not redirect_uri:
            raise ValueError('redirect_uri cannot be empty')
        if not scope:
            raise ValueError('scope cannot be empty')

        encoded_redirect_uri = quote(redirect_uri, safe=':/')
        encoded_scope = quote(scope, safe=',')
        auth_url = (
            'http://www.strava.com/oauth/authorize'
            f'?client_id={client_id}'
            '&response_type=code'
            f'&redirect_uri={encoded_redirect_uri}'
            f'&scope={encoded_scope}'
        )

        if state:
            auth_url += f'&state={quote(str(state), safe="")}'

        if approval_prompt:
            auth_url += f'&approval_prompt={quote(str(approval_prompt), safe="")}'

        return auth_url

    @staticmethod
    def parse_authorization_response(redirected_url: str) -> dict:
        if not redirected_url or not redirected_url.strip():
            raise ValueError('redirected_url cannot be empty')

        parsed_url = urlparse(redirected_url.strip())
        query_params = parse_qs(parsed_url.query)

        error = (query_params.get('error') or [''])[0].strip() or None
        code = (query_params.get('code') or [''])[0].strip() or None
        state = (query_params.get('state') or [''])[0].strip() or None
        scopes = [scope.strip() for scope in ((query_params.get('scope') or [''])[0]).split(',') if scope.strip()]

        if error:
            raise ValueError(f'Strava authorization failed: {error}')

        if not code:
            raise ValueError('Authorization code not found in redirected URL')

        return {
            'code': code,
            'state': state,
            'scope': scopes,
            'redirected_url': redirected_url.strip(),
        }

    @staticmethod
    def parse_authorization_code(redirected_url: str) -> str:
        return StravaData.parse_authorization_response(redirected_url)['code']
    
    @staticmethod
    def exchange_authorization_code(client_id: int, client_secret: str, code: str) -> dict:
        response = requests.post(
            url='https://www.strava.com/api/v3/oauth/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'code': code,
                'grant_type': 'authorization_code',
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def refresh_access_token(client_id: int, client_secret: str, refresh_token: str) -> dict:
        response = requests.post(
            url='https://www.strava.com/api/v3/oauth/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token',
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def build_token_payload(token_response: dict) -> dict:
        athlete = token_response.get('athlete') or {}
        return {
            'token_type': token_response.get('token_type'),
            'access_token': token_response.get('access_token'),
            'refresh_token': token_response.get('refresh_token'),
            'expires_at': token_response.get('expires_at'),
            'expires_in': token_response.get('expires_in'),
            'athlete': {
                'id': athlete.get('id'),
                'username': athlete.get('username'),
                'firstname': athlete.get('firstname'),
                'lastname': athlete.get('lastname'),
            },
        }

    @staticmethod
    def complete_oauth(
        client_id: int,
        client_secret: str,
        redirected_url: str,
        expected_state: Optional[str] = None,
    ) -> dict:
        auth_response = StravaData.parse_authorization_response(redirected_url)

        if expected_state and auth_response.get('state') != expected_state:
            raise ValueError('State mismatch in redirected URL')

        token_response = StravaData.exchange_authorization_code(
            client_id=client_id,
            client_secret=client_secret,
            code=auth_response['code'],
        )

        result = {
            'authorization': auth_response,
            'tokens': StravaData.build_token_payload(token_response),
        }
        return result

    @staticmethod
    def get_strava_tokens(client_id, client_secret, code):
        return StravaData.exchange_authorization_code(client_id, client_secret, code)

    @staticmethod
    def get_strava_data(access_token: str) -> dict:
        """
        Obtiene datos del perfil del ciclista y sus actividades de Strava.
        Calcula métricas clave como CTL, ATL, TSB, W/kg, etc.

        Parámetros
        ----------
        access_token : str
            Token de acceso de Strava.

        Retorna
        -------
        dict
            Un diccionario con datos relevantes del ciclista y sus actividades.
        """
        headers = {'Authorization': f'Bearer {access_token}'}

        # 1. Obtener perfil del atleta
        athlete_response = requests.get('https://www.strava.com/api/v3/athlete', headers=headers)
        athlete_response.raise_for_status()
        athlete_profile = athlete_response.json()
        weight_kg = athlete_profile.get('weight', 70.0) # Usar 70kg por defecto si no está

        # 2. Obtener actividades (últimas 200 para calcular CTL/ATL)
        activities_response = requests.get(
            'https://www.strava.com/api/v3/athlete/activities',
            headers=headers,
            params={'per_page': 200, 'after': int((datetime.now(timezone.utc) - timedelta(days=90)).timestamp())}
        )
        activities_response.raise_for_status()
        activities = activities_response.json()

        # 3. Procesar actividades
        processed_activities = []
        daily_tss = { (datetime.now(timezone.utc) - timedelta(days=i)).strftime('%Y-%m-%d'): 0.0 for i in range(90, -1, -1) }

        # Inicializar con valores razonables para evitar NaN si no hay datos recientes
        current_ftp_estimate = 250.0 # Valor por defecto
        current_avg_hr_7d    = 140.0 # Valor por defecto
        recent_activity_count = 0
        sum_avg_power_7d = 0.0
        sum_avg_hr_7d    = 0.0

        for i, activity in enumerate(activities):
            if activity['type'] == 'Ride':
                activity_date_str = datetime.strptime(activity['start_date_local'], '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%d')
                tss = activity.get('suffer_score', activity.get('kilojoules', 0) / 4.184) # Simplificado, idealmente via TP

                # Si no tenemos TSS de Strava, estimamos un TSS muy básico
                if tss == 0 and activity.get('moving_time', 0) > 0 and activity.get('average_watts', 0) > 0:
                    # Estimación muy ruda: TSS = (IF^2) * tiempo_en_horas * 100
                    # Asumiendo IF = avg_watts / ftp (necesitamos un FTP)
                    # Para esta estimación, usaremos un IF fijo o lo ajustaremos iterativamente
                    # De momento, un valor genérico para evitar 0
                    tss = (activity['moving_time'] / 3600) * 60 # 60 TSS/hr base

                processed_activities.append({
                    'date':          activity_date_str,
                    'moving_time':   activity.get('moving_time', 0),
                    'distance_m':    activity.get('distance', 0),
                    'elevation_m':   activity.get('total_elevation_gain', 0),
                    'avg_watts':     activity.get('average_watts', 0),
                    'avg_hr':        activity.get('average_heartrate', 0),
                    'max_hr':        activity.get('max_heartrate', 0),
                    'tss':           tss,
                })
                daily_tss[activity_date_str] += tss

                # Calcular métricas de 7 días
                # Make activity_date timezone aware (UTC) to match datetime.now(timezone.utc)
                activity_date = datetime.strptime(activity_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - activity_date).days <= 7:
                    if activity.get('average_watts', 0) > 0: # Solo si hay datos de potencia
                        sum_avg_power_7d += activity['average_watts']
                        current_ftp_estimate = max(current_ftp_estimate, activity['average_watts']) # Ruda estimación de FTP
                    if activity.get('average_heartrate', 0) > 0: # Solo si hay datos de HR
                        sum_avg_hr_7d += activity['average_heartrate']
                    recent_activity_count += 1

        # 4. Calcular CTL, ATL, TSB
        ctl_values = []
        atl_values = []
        tss_list = [daily_tss[d] for d in sorted(daily_tss.keys())]

        # Asumiendo un estado inicial, o un valor por defecto si no hay datos pasados
        ctl = 0.0
        atl = 0.0

        if len(tss_list) > 0:
            for tss_day in tss_list:
                ctl = ctl + (tss_day - ctl) * (1 - np.exp(-1/42))
                atl = atl + (tss_day - atl) * (1 - np.exp(-1/7))
                ctl_values.append(ctl)
                atl_values.append(atl)

        # Últimos valores calculados
        current_ctl = ctl_values[-1] if ctl_values else 0.0
        current_atl = atl_values[-1] if atl_values else 0.0
        current_tsb = current_ctl - current_atl

        # Días de descanso seguidos
        rest_days = 0
        for tss in reversed(tss_list):
            if tss == 0:
                rest_days += 1
            else:
                break

        # Calcular promedios de 7 días
        avg_power_7d = sum_avg_power_7d / recent_activity_count if recent_activity_count > 0 else 0.0
        avg_hr_7d    = sum_avg_hr_7d    / recent_activity_count if recent_activity_count > 0 else 0.0

        # Simplificación: W/kg basado en FTP y peso
        wpkg_7d = (current_ftp_estimate / weight_kg) if weight_kg > 0 else 0.0

        return {
            'activities': processed_activities,
            'ctl':        current_ctl,
            'atl':        current_atl,
            'tsb':        current_tsb,
            'avg_power_7d': avg_power_7d,
            'avg_hr_7d':    avg_hr_7d,
            'ftp_estimate': current_ftp_estimate,
            'weight_kg':    weight_kg,
            'wpkg_7d':      wpkg_7d,
            'rest_days':    rest_days,
            'daily_tss':    tss_list, # Para depuración o análisis si es necesario
        }
    
    @staticmethod
    def train_model(env: gym.Env, save_path: str = 'cycling_ppo', total_timesteps: int = 100_000):
        """
        Entrena un modelo PPO sobre el entorno CyclingEnv y lo guarda en disco.

        Parámetros
        ----------
        env            : instancia de CyclingEnv ya creada
        save_path      : ruta donde guardar el modelo (sin extensión .zip)
        total_timesteps: pasos totales de entrenamiento (default 100k)
        """
        from stable_baselines3 import PPO
        from stable_baselines3.common.env_checker import check_env

        print("🔍 Verificando entorno...")
        check_env(env, warn=True)

        print("🚀 Iniciando entrenamiento PPO...")
        model = PPO(
            policy        = "MlpPolicy",
            env           = env,
            learning_rate = 3e-4,
            n_steps       = 2048,
            batch_size    = 64,
            n_epochs      = 10,
            gamma         = 0.99,
            verbose       = 1,
        )

        model.learn(total_timesteps=total_timesteps)
        model.save(save_path)
        print(f"✅ Modelo guardado en '{save_path}.zip'")
        return model

    @staticmethod
    def make_predictions(model_path: str, env: gym.Env, steps: int = 7):
        """
        Carga el modelo guardado y realiza predicciones para los próximos N días.

        Parámetros
        ----------
        model_path : str
            Ruta al archivo .zip del modelo (ej: 'cycling_ppo')
        env : gym.Env
            Instancia del entorno CyclingEnv
        steps : int
            Número de días a predecir
        """
        from stable_baselines3 import PPO

        try:
            # Cargar el modelo entrenado
            model = PPO.load(model_path)
            print(f"✅ Modelo '{model_path}' cargado exitosamente.\n")

            # Reiniciar/obtener estado actual
            obs, _ = env.reset()
            print(obs)

            print(f"📅 Planificación sugerida para los próximos {steps} días:")
            print("="*60)

            for d in range(steps):
                # El modelo predice la mejor acción basada en la observación actual
                # IMPORTANTE: action es un ndarray, necesitamos el valor escalar
                action, _states = model.predict(obs, deterministic=True)
                action_scalar = int(action)

                # Ejecutar la acción en el entorno para obtener el siguiente estado
                obs, reward, terminated, truncated, info = env.step(action_scalar)

                # Traducir la acción a texto legible usando el diccionario del entorno
                action_name = env.unwrapped._ACTION_NAMES[action_scalar]

                print(f"Día {d+1:>2}: {action_name:<30} | Recompensa: {reward:>6.2f} | TSB: {info['tsb']:>5.1f}")

                if terminated or truncated:
                    break

            print("="*60)

        except FileNotFoundError:
            print(f"❌ No se encontró el archivo {model_path}. Asegúrate de haber entrenado el modelo primero.")

    @staticmethod
    def run_training_pipeline(
        client_id: int,
        client_secret: str,
        code: str,
        save_path: str = 'cycling_ppo',
        total_timesteps: int = 100_000,
        prediction_steps: int = 7,
        render_mode: Optional[str] = None,
    ) -> dict:
        strava_tokens = StravaData.exchange_authorization_code(client_id, client_secret, code)
        access_token = strava_tokens.get('access_token')
        if not access_token:
            raise ValueError(f'Strava token exchange failed: {strava_tokens}')

        strava_data = StravaData.get_strava_data(access_token)
        env = CyclingEnv(strava_data=strava_data, render_mode=render_mode)
        StravaData.train_model(env, save_path=save_path, total_timesteps=total_timesteps)
        StravaData.make_predictions(f'{save_path}.zip', env, steps=prediction_steps)

        return {
            'save_path': save_path,
            'prediction_steps': prediction_steps,
            'total_timesteps': total_timesteps,
            'strava_data': strava_data,
            'tokens': StravaData.build_token_payload(strava_tokens),
        }
        
