#include <Arduino.h>
#include "Modulino.h"
#include <Arduino_RouterBridge.h>

ModulinoThermo thermo;
ModulinoDistance distance;

const float TEMP_HIGH = 27.0;
const float TEMP_LOW  = 16.0;
const float DIST_NEAR = 200.0;
//const unsigned long BATHROOM_TIMEOUT = 180000UL; // 3 minutos en ms
const unsigned long BATHROOM_TIMEOUT = 180UL; // 3 minutos en ms


bool tempAlertActive = false;
bool distAlertActive = false;

// Estado del temporizador del baño
bool timerActive = false;
unsigned long timerStart = 0;

void setup() {
  Bridge.begin();
  Monitor.begin();
  Modulino.begin();
  thermo.begin();
  distance.begin();  
}

void loop() {
  float temperature = thermo.getTemperature();
  

  // --- Temperatura ---
  bool tempOutOfRange = (temperature > TEMP_HIGH || temperature < TEMP_LOW);
  if (tempOutOfRange && !tempAlertActive) {
    int severity = 1;
    if (temperature > TEMP_HIGH + 2 || temperature < TEMP_LOW - 2) severity = 2;
    if (temperature > TEMP_HIGH + 3 || temperature < TEMP_LOW - 3) severity = 3;

    Bridge.notify("temp_alert", temperature, severity);
    tempAlertActive = true;
    Monitor.print("ALERTA temp enviada: ");
    Monitor.println(temperature);
  } else if (!tempOutOfRange && tempAlertActive) {
    Bridge.notify("temp_normal", temperature);
    tempAlertActive = false;
  }

  // --- Detección de paso por el umbral ---
  if (distance.available()) {
    float dist = distance.get();
    bool somethingNear = (dist < DIST_NEAR);

    if (somethingNear && !distAlertActive) {
      // Flanco de subida: alguien acaba de pasar
      distAlertActive = true;
      Monitor.print("PASO detectado a: ");
      Monitor.println(dist);

      if (!timerActive) {
        // Primera pasada: arranca el temporizador (entró)
        timerActive = true;
        timerStart = millis();
        Monitor.println("⏱️  Temporizador iniciado (entrada al baño)");
      } else {
        // Segunda pasada: la persona ha salido, cancela el temporizador
        timerActive = false;
        unsigned long elapsed = millis() - timerStart;
        Monitor.print("✅ Salida detectada, tiempo dentro: ");
        Monitor.print(elapsed / 1000);
        Monitor.println("s");
      }
    } else if (!somethingNear && distAlertActive) {
      distAlertActive = false;
    }
  }

  else{
    if (distAlertActive){
      distAlertActive = false;
    }
  }
  // --- Comprobar timeout del baño ---
  if (timerActive && (millis() - timerStart > BATHROOM_TIMEOUT)) {
    Bridge.notify("bathroom_timeout");
    Monitor.println("🚨 ALERTA: persona lleva más de 3 min en el baño");
    timerActive = false;  // Reset para no spamear
  }

  delay(50);
}
