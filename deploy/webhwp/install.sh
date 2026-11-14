#!/usr/bin/env bash
# 한컴 웹 한글 기안기 V2 설치 — 앱 서버(AURA 와 같은 계정)에. 한컴 「신규 설치 가이드 v1.0」 3장 순서를 sudo 없이 사용자 서비스로 옮긴 것.
# 패키지·라이선스는 기관이 한컴에서 받는 상용 파일이라 저장소·설치 묶음에 넣지 않는다.
#
#   bash deploy/webhwp/install.sh <webhwpctrl 패키지.zip> <apache-tomcat-9.x.zip> <license.dat> [포트(기본 8090)]
#
# 하는 일(다시 돌려도 같은 결과): ① Java 8·ImageMagick·cryptopp — sudo 가 되면 apt, 안 되면 패키지를 받아 ~/opt/webhwp/sysroot 에 풀기
#   ② Tomcat(127.0.0.1:포트만) ③ 패키지 풀기·필터 서버 ④ 설정(저장 경로·필터 서버·라이선스) ⑤ war 배포 ⑥ 사용자 서비스 2개 ⑦ 동작 확인
# 브라우저는 기안기에 바로 닿지 않는다 — AURA 가 /webhwpctrl/ 로 넘겨 준다(로그인한 사람만). 포트를 바꾸면 .env.local 에
# ZZAIMY_WEBHWP_URL=http://127.0.0.1:<포트> 를 넣는다.
set -u
PKG="${1:?한컴 패키지 zip}"; TOMCAT_ZIP="${2:?Tomcat 9 zip}"; LIC="${3:?license.dat}"; PORT="${4:-8090}"
W="$HOME/opt/webhwp"; INST="$W/inst"; TARGET="$W/webhwpctrl"; CONF="$HOME/.webhwpctrl_v2"
step() { echo; echo "── $(date '+%T') $*"; }
fail() { echo "설치 실패: $*" >&2; exit 1; }
for f in "$PKG" "$TOMCAT_ZIP" "$LIC"; do [ -s "$f" ] || fail "$f 없음"; done
mkdir -p "$W"
can_sudo() { [ "$(id -u)" = 0 ] || sudo -n true 2>/dev/null; }
SUDO=$([ "$(id -u)" = 0 ] || echo sudo)

step "① 실행 환경(Java 8 · ImageMagick · cryptopp)"
PKGS="openjdk-8-jre-headless imagemagick libcrypto++8"
if can_sudo && $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q $PKGS >/dev/null 2>&1; then
  JH=$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")
  printf 'export JAVA_HOME=%s\nexport PATH=$JAVA_HOME/bin:$PATH\n' "$JH" > "$W/env.sh"
else
  # sudo 없이 — 패키지 파일만 받아(apt-get download) 홈 아래에 푼다. 운영 서버(Ubuntu 22.04)에서 쓰는 방식
  mkdir -p "$W/debs" "$W/sysroot"
  (cd "$W/debs" && apt-get download $PKGS $(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks \
     --no-replaces --no-enhances $PKGS 2>/dev/null | grep '^\w' | grep -E 'magick|lcms|lqr|fftw|ltdl|jbig|tiff|webp|openjp|heif|raw|djvu|wmf|gomp|crypto' | sort -u) >/dev/null 2>&1) \
    || fail "패키지를 받지 못했습니다(인터넷·apt 설정 확인)"
  for d in "$W"/debs/*.deb; do dpkg -x "$d" "$W/sysroot"; done
  cat > "$W/env.sh" <<EOF
export JAVA_HOME=$W/sysroot/usr/lib/jvm/java-8-openjdk-amd64/jre
export PATH=\$JAVA_HOME/bin:\$PATH
export LD_LIBRARY_PATH=$W/sysroot/usr/lib/x86_64-linux-gnu\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
export MAGICK_CONFIGURE_PATH=$(ls -d "$W"/sysroot/etc/ImageMagick-* 2>/dev/null | head -1)
EOF
fi
. "$W/env.sh"
java -version 2>&1 | head -1 || fail "java 실행 안 됨"

step "② Tomcat (127.0.0.1:$PORT)"
systemctl --user stop webhwp-filter.service webhwp-tomcat.service 2>/dev/null
if [ ! -x "$W/tomcat/bin/startup.sh" ]; then
  rm -rf "$W/tomcat-new" && mkdir -p "$W/tomcat-new" && unzip -q -o "$TOMCAT_ZIP" -d "$W/tomcat-new" || fail "Tomcat 압축 풀기"
  mv "$(ls -d "$W"/tomcat-new/apache-tomcat-*)" "$W/tomcat" && rm -rf "$W/tomcat-new"
fi
# 외부에 열지 않는다 — 연결기는 127.0.0.1 에만, 종료 포트는 PORT+5
sed -i -E "s|<Connector port=\"[0-9]+\" protocol=\"HTTP/1.1\"|<Connector port=\"$PORT\" address=\"127.0.0.1\" protocol=\"HTTP/1.1\"|" "$W/tomcat/conf/server.xml"
sed -i -E "s|<Server port=\"[0-9]+\"|<Server port=\"$((PORT + 5))\"|" "$W/tomcat/conf/server.xml"
printf '. %s/env.sh\n' "$W" > "$W/tomcat/bin/setenv.sh"
rm -rf "$W"/tomcat/webapps/{docs,examples,host-manager,manager}
chmod +x "$W"/tomcat/bin/*.sh
grep -q "port=\"$PORT\"" "$W/tomcat/conf/server.xml" || fail "server.xml 포트 설정"

step "③ 패키지·필터 서버"
mkdir -p "$W/data/upload" "$W/data/save" "$W/data/temp" "$W/data/dept"
if [ ! -f "$INST/data/webapp/webhwpctrl.war" ]; then
  rm -rf "$INST" && mkdir -p "$INST" && unzip -q -o "$PKG" -d "$INST" || fail "패키지 압축 풀기"
fi
mkdir -p "$CONF/conf" "$CONF/license"
[ -e "$CONF/conf/webhwpctrl.properties" ] || cp -rf "$INST"/data/HwpSdk/conf/* "$CONF/conf/"
printf 'TARGET_DIR=%s\nTOMCAT_ACCOUNT=%s\n' "$TARGET" "$(id -un)" > "$CONF/conf/manager.properties"
rm -rf "$TARGET.tmp" && mkdir -p "$TARGET.tmp"
cp -rf "$INST/data/HwpSdk/HwpSdk" "$INST/data/HwpSdk/webhwp-filter-service" "$TARGET.tmp/" || fail "필터 서버 복사"
rm -rf "$TARGET" && mv "$TARGET.tmp" "$TARGET" && chmod +x "$TARGET"/webhwp-filter-service/*.sh

step "④ 설정·라이선스"
P="$CONF/conf/webhwpctrl.properties"
sed -i "s|^hncPath=.*|hncPath=$TARGET/HwpSdk|; s|^uploadPath=.*|uploadPath=$W/data/upload|; s|^savePath=.*|savePath=$W/data/save|; s|^tempPath=.*|tempPath=$W/data/temp|" "$P"
sed -i "s|^filterServer *=.*|filterServer = http://localhost:$PORT/filterserver|; s|^licensePath *=.*|licensePath = $CONF/license/|" "$P"
install -m 600 "$LIC" "$CONF/license/license.dat"
printf '1,%s,webhwpctrl,127.0.0.1,%s,\n2,%s,filterserver,127.0.0.1,%s,\n' "$(id -un)" "$PORT" "$(id -un)" "$PORT" > "$CONF/conf/server.list"

step "⑤ war 배포"
cp "$INST"/data/webapp/{filterserver,webhwpctrl,webhwpctrl-admin}.war "$W/tomcat/webapps/"

step "⑥ 사용자 서비스(재부팅 뒤에도 — sudo loginctl enable-linger $(id -un))"
U="$HOME/.config/systemd/user"; mkdir -p "$U"
for f in webhwp-tomcat webhwp-filter; do
  sed -e "s|@W@|$W|g" -e "s|@TARGET@|$TARGET|g" -e "s|@PORT@|$PORT|g" "$(dirname "$0")/$f.service.in" > "$U/$f.service"
done
systemctl --user daemon-reload
systemctl --user enable -q webhwp-tomcat.service webhwp-filter.service
systemctl --user restart webhwp-tomcat.service; sleep 20
systemctl --user restart webhwp-filter.service; sleep 15

step "⑦ 동작 확인"
for i in $(seq 1 30); do
  v=$(curl -s --max-time 5 "http://127.0.0.1:$PORT/webhwpctrl/version.txt") && [ -n "$v" ] && break; sleep 5
done
echo "   기안기 판: ${v:-응답 없음} · 필터 서버: $(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/filterserver/")"
[ "$PORT" = 8090 ] || echo "   .env.local 에 ZZAIMY_WEBHWP_URL=http://127.0.0.1:$PORT 를 넣고 systemctl --user restart zzaimy"
[ -n "${v:-}" ] && echo "설치 끝 — AURA 한글 문서함(/hwp)에서 문서를 열어 확인" || fail "기안기가 응답하지 않습니다(journalctl --user -u webhwp-tomcat)"
