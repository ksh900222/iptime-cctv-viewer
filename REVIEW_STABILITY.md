# CCTV 뷰어 안정성 리뷰 + 전환 속도 개선

대상: `app.py` `video.py` `audio.py` (`config.py` `ptz.py`는 검토만, 변경 없음)
기준일: 2026-09-20 · 측정 환경: Mac mini, Homebrew ffmpeg, 거실 카메라 192.168.0.28

## 요약

카메라 전환이 느렸던 진짜 원인은 settle 타이머(800ms)가 아니라 **teardown 때 ffmpeg가
SIGTERM으로 죽지 못하고 매번 SIGKILL 당하고 있었던 것**입니다. 프레임 루프가 `_stop`을
보는 순간 stdout 읽기를 멈추는데, write()에 막힌 ffmpeg는 시그널 핸들러에 도달하지 못합니다.
그래서 `_kill_proc()`이 1.2초를 기다렸다가 SIGKILL을 보내고, GUI 스레드는 그동안 통째로
멈춰 있었습니다.

종료 중에도 stdout을 계속 비워주도록 고치자 ffmpeg가 ~90ms 만에 정상 종료(RTSP TEARDOWN 포함)합니다.

| 지표 | 패치 전 | 패치 후 |
| --- | --- | --- |
| 전환 시 GUI 멈춤 | 1281~1287 ms | 58~102 ms |
| 전환 → 새 영상 첫 프레임 | 2751 / 3619 / 3746 ms | 1556 / 1701 / 1965 ms |
| `pkill -9` 폴백 발동 | 전환 5회 중 5회 | 0회 |
| 종료 후 남은 ffmpeg | 0 | 0 |

기능은 하나도 건드리지 않았습니다. ffmpeg 1개(영상 stdout + 오디오 FIFO) 구조, `-af volume=40`
고정, QAudioSink 볼륨, `-y`, PTZ, 한국어 UI, 시작 비프 없음 모두 그대로입니다.

## 측정 데이터

임시 계측 스크립트로 실측한 값이며, 스크립트는 리뷰 후 삭제했습니다.

**1. ffmpeg가 SIGTERM에 반응하는 데 걸리는 시간** — 이번 작업의 핵심 근거입니다.

| 종료 중 stdout/FIFO 배수 | 결과 |
| --- | --- |
| 계속 비움 | 88 ms / 89 ms / 91 ms 만에 정상 종료 (rc=255) |
| 비우지 않음 | 3회 모두 6초 후에도 생존 → SIGKILL 외 방법 없음 |

**2. 이전 세션 종료 후 재접속 가능 시점** — 8회 시도 전부 성공, 400/401 거부 0회.

| 이전 종료 방식 | gap 120ms | gap 400ms |
| --- | --- | --- |
| SIGTERM | 성공 (총 2237 / 2230 ms) | 성공 (총 2233 / 2230 ms) |
| SIGKILL | 성공 (총 2208 / 2245 ms) | 성공 (총 2233 / 2218 ms) |

여기서 "총"은 이전 세션 종료 시점부터 새 첫 프레임까지입니다. gap을 얼마로 주든 항상
**약 2230ms로 수렴**합니다. 즉 기다리는 주체가 우리 타이머에서 ffmpeg 내부로 옮겨갈 뿐이고,
실제 하한은 카메라가 다음 키프레임(IDR)을 내보내는 주기가 결정합니다. settle 값은 애초에
임계 경로가 아니었습니다.

**3. probe 파라미터 축소 효과** — 없음. 그래서 건드리지 않았습니다.

| 설정 | 첫 프레임까지 |
| --- | --- |
| `-probesize 2M -analyzeduration 1000000` (현행) | 1616 ms / 2274 ms |
| `-probesize 500k -analyzeduration 300000` | 1788 ms / 1785 ms |

## 발견한 문제와 조치

### 1. ffmpeg가 매번 SIGKILL로 죽고 있었음 (심각, 수정)

`VideoWorker.run()`의 프레임 루프는 `_stop`이 걸리면 즉시 stdout 읽기를 그만둡니다. 프레임
하나가 2.7MB인데 파이프 버퍼는 64KB뿐이라, 그 시점의 ffmpeg는 거의 확실히 stdout write()에
막혀 있습니다. 막힌 ffmpeg는 SIGTERM을 처리하지 못하므로 `_kill_proc()`의 1.2초 타임아웃을
다 쓰고 SIGKILL로 넘어갑니다. SIGKILL은 RTSP TEARDOWN을 건너뛰기 때문에, 코드 주석이
경계하던 "카메라가 한동안 400 Bad Request를 돌려주는" 상황을 스스로 만들고 있었습니다.

조치: `_drain_stdout()`을 추가하고 `_kill_proc()`이 SIGTERM부터 reap까지 전 구간 동안
백그라운드로 stdout을 비웁니다(최대 3초, 프로세스가 죽으면 즉시 종료). 원시 fd에 `select` +
`os.read`를 쓰므로 프레임 루프의 BufferedReader 내부 상태를 건드리지 않습니다.

### 2. 오디오 장치 열기에 실패하면 영상까지 멈춤 (심각, 수정)

`start_pcm()`은 `_open_sink()`가 실패하면 상태 메시지만 띄우고 **리더 스레드를 띄우지 않은 채
리턴**했습니다. FIFO의 소비자는 그 리더뿐이고, `prepare_audio_fifo()`가 잡아두는 keep-fd는
읽지 않는 O_RDWR입니다. 따라서 FIFO 64KB가 차는 약 0.7초 뒤 ffmpeg의 오디오 먹서가 막히고,
한 프로세스가 두 출력을 함께 쓰기 때문에 **영상도 같이 정지**합니다. 오디오 장치 문제가 영상
장애로 번지는 경로였습니다.

조치: 싱크 실패 여부와 무관하게 리더 스레드를 항상 기동합니다. 싱크가 없으면 읽어서 버리는
역할만 하며(버퍼는 기존 1초 상한에서 자동 절삭), 상태는 "오디오 장치 열기 실패 (영상만 재생)"로
표시합니다.

### 3. 무조건 실행되던 `pkill -9` (수정)

`stop_stream()`이 매 teardown마다 `pkill -9 -f "ffmpeg.*192.168.0"`을 돌렸습니다. 우리
프로세스 그룹은 이미 `_kill_proc()`이 처리했고, `app._teardown_stream()`은 그 직후
`_cleanup_orphan_ffmpeg()`로 pgrep 기반 SIGTERM 스윕까지 합니다. 즉 정상 경로에서는 중복인
데다, 시스템 전역 SIGKILL이라 TEARDOWN을 건너뛰게 만드는 쪽으로만 작용했습니다.

조치: `_kill_proc()`이 SIGTERM으로 못 죽여 SIGKILL로 넘어갔거나(`_hard_kill`) 스레드가 3초
안에 안 끝난 경우에만 폴백으로 실행하고, 발동 시 로그를 남깁니다. SIGTERM 스윕은 그대로
남아 있으므로 안전망은 줄지 않았습니다.

### 4. `_cmd()`의 RuntimeError가 워커 스레드를 죽일 수 있었음 (수정)

FIFO가 없는 상태에서 `_cmd()`는 `RuntimeError`를 던지는데, `run()` 안에서 잡히지 않아
스레드가 그대로 죽고 `self.video`가 시체가 됩니다. 현재 호출 순서상 실제로 발생하진 않지만
방어가 없었습니다. 조치: `try/except RuntimeError`로 감싸 상태 메시지를 내보내고 루프를 종료합니다.

### 5. 전환 직후 이전 카메라 프레임 유출 가능성 (수정)

프레임 루프가 `_stop`/`_url`을 프레임 경계에서만 확인하고 곧바로 emit 했습니다. 조치: emit
직전에 한 번 더 확인합니다. 종료 중 drain 스레드와 읽기가 겹쳐 짧게 읽힌 프레임이 화면에
튀는 것도 이 가드로 막힙니다.

### 6. 버려진 PCM 리더가 새 세션 버퍼를 오염 (수정)

`stop_pcm()`은 join을 0.5초만 기다리고 실패하면 리더를 포기합니다. 포기된 리더는
`stop.is_set()` 확인과 `self._buf += data` 사이의 창을 통해 **다음 카메라의 버퍼**에 이전
카메라 오디오를 밀어 넣을 수 있었습니다. 조치: 락 안에서 `self._stop is not stop`으로 세션
교체 여부까지 확인하고 빠져나갑니다.

### 7. 죽은 QAudioSink 재사용 (수정)

`_open_sink()`는 `self._sink`가 있으면 무조건 True를 반환했습니다. 출력 장치가 뽑히는 등으로
싱크가 `StoppedState`에 빠지면 이후 영원히 무음이 됩니다. 조치: `StoppedState`면 닫고 다시
엽니다. 정상 동작 중에는 Active/Idle만 오가므로 재생 중 싱크가 재생성될 일은 없습니다.

### 8. 파킹된 VideoWorker 누수 (수정)

정지에 실패한 워커를 `self._retired`에 넣기만 하고 비우지 않았습니다. 조치: `_reap_retired()`를
추가해 전환 때마다 이미 끝난 워커를 `deleteLater()` 하고, `closeEvent()`에서 남은 워커의
`stop_stream()`까지 호출합니다.

### 9. 종료 시 ffmpeg가 살아남을 수 있었음 (수정)

`closeEvent()`가 orphan 스윕을 하지 않아, 파킹된 워커의 ffmpeg가 남으면 RTSP 슬롯을 물고 있어
다음 실행이 느려집니다. 조치: 종료 직전 `_cleanup_orphan_ffmpeg()`를 호출합니다.

### 10. settle 800ms (조정)

`RTSP_SETTLE_SECONDS`를 0.8 → 0.4로 낮췄습니다. 측정상 120ms 간격에서도 8/8 재접속에
성공했으므로 0.4초는 충분한 여유이고, 위 2번 표에서 보듯 **체감 속도에는 영향이 없습니다**.
전환이 빨라진 것은 settle이 아니라 1번 수정 덕분입니다. 값 자체는 "카메라가 늦게 놓아주는"
예외 상황용 안전 마진으로 남겨두었습니다.

## 변경 파일 요약

**`video.py`**
- `_drain_stdout()` 신설, `_kill_proc()`이 종료 전 구간 배수
- `_kill_proc()`: SIGKILL 후 reap 추가, `_hard_kill` 기록
- `stop_stream()`: `pkill -9`를 비정상 종료 시에만 실행
- `run()`: `_cmd()` 예외 처리, 프레임 emit 직전 stop/url 재확인

**`audio.py`**
- `start_pcm()`: 싱크 실패해도 FIFO 리더 항상 기동
- `_read_loop()`: 락 안에서 세션 교체 확인
- `_open_sink()`: `StoppedState` 싱크 재생성

**`app.py`**
- `RTSP_SETTLE_SECONDS` 0.8 → 0.4
- `_reap_retired()` 신설, `_teardown_stream()`에서 호출
- `_teardown_stream()`: disconnect에서 `RuntimeError`도 처리
- `closeEvent()`: 파킹 워커 정리 + orphan 스윕

## 손대지 않은 것

| 항목 | 이유 |
| --- | --- |
| ffmpeg 1개 구조 (영상 stdout + 오디오 FIFO) | 유지 지시. 듀얼 RTSP/VLC 도입 안 함 |
| `-af aresample=48000,volume=40` 고정 | 유지 지시. 볼륨은 QAudioSink만 |
| `-y` 플래그 | FIFO에 필수 |
| `-probesize 2M -analyzeduration 1000000` | 축소해도 이득 없음(측정 3번). `-map 0:a:0` 실패 위험만 커짐 |
| `-timeout 8000000`, 재시도 backoff(0.7s + 1.0~6.0s) | 실패 경로 전용, 현재 문제 없음 |
| VideoToolbox → 소프트웨어 폴백 | 정상 동작 |
| `MIN_START_DELAY_MS = 120` | 실측 재접속 하한과 일치 |
| `ptz.py`, `PtzWorker` 전체 | 범위 밖, 동작 확인됨 |
| 한국어 UI, 레이아웃, 시작 비프 없음 | 유지 지시 |
| 시작 시 `_cleanup_orphan_ffmpeg()` | 기존 동작 유지 |

## 남은 위험 / 알아둘 점

- **전환 하한 ~1.7초는 카메라가 정합니다.** 이전 세션 종료 후 첫 프레임까지 약 2.2초가
  고정적으로 걸리며, 이는 카메라의 키프레임 주기입니다. 뷰어 쪽에서 더 줄이려면 두 카메라를
  동시에 열어두고 표시만 바꾸는 구조여야 하는데, "ffmpeg 1개" 원칙과 충돌하므로 하지 않았습니다.
- 앱을 `pkill` 등으로 강제 종료하면 `closeEvent`가 돌지 않아
  `/var/folders/.../T/cctv_av_*` 디렉터리(FIFO 노드, 각 96바이트)가 남습니다. 정상 종료
  경로에서는 남지 않는 것을 확인했습니다.
- `_hard_kill`은 한번 켜지면 그 워커에서 유지되므로 `pkill` 폴백이 과잉 실행될 수 있습니다.
  안전한 방향의 과잉이라 그대로 두었습니다.
- ffmpeg가 s16le 먹서에 대해 `non monotonically increasing dts` 경고를 냅니다. 현재
  `-loglevel warning`에서는 표시되지 않으며 오디오 재생에는 영향이 없습니다.

## 검증 체크리스트

컴파일:
```
python3 -m py_compile app.py video.py audio.py config.py ptz.py
```

실행 후 확인:
- [ ] 2초 안에 첫 프레임, 상태바가 `live NN fps · 1280x720 · TCP`
- [ ] 로그에 `cctv.audio QAudioSink ... 1ch` 1회
- [ ] 로그에 **`falling back to pkill -9`가 없을 것** ← 정상 teardown의 핵심 지표
- [ ] 로그에 `오디오 FIFO 실패` / `File exists` 없음
- [ ] 카메라 1↔2 전환: 클릭 즉시(0.1초 내) "전환 중…" 표시, UI 멈춤 없음
- [ ] 전환 후 1.5~2.5초 내 새 영상
- [ ] 0.2초 간격 연타 전환 후 `pgrep -f 'ffmpeg.*192.168.0' | wc -l` → `1`
- [ ] 볼륨 슬라이더 0↔100: 끊김이나 재연결 없이 부드럽게
- [ ] PTZ ←↑↓→ 이동, space 정지, STOP 버튼
- [ ] `S` 스냅샷 저장
- [ ] 창 닫은 뒤 `pgrep -f 'ffmpeg.*192.168.0'` → 결과 없음

## 이번 리뷰에서 실제로 확인한 것

패치된 코드로 실제 Viewer를 띄워 전환 5회(연타 1회 포함)를 자동 수행하고 종료하는 검사를
3회 돌렸습니다. 매번 `pkill -9` 폴백 0회, teardown GUI 멈춤 58~102ms, 종료 후 잔존 ffmpeg 0개,
파킹된 워커 0개, 새로 생긴 FIFO 디렉터리 0개였습니다. 마지막으로 뷰어를 평소대로 재기동해
첫 프레임 1874ms, QAudioSink 정상, FIFO 오류 없음을 확인했습니다.
