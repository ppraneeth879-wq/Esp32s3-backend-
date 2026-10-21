#include "MicManager.h"
#include "ESP_I2S.h"

static I2SClass I2S;

bool MicManager::begin() {
    I2S.setPinsPdmRx(MIC_CLK_PIN, MIC_DATA_PIN);
    return I2S.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
}

size_t MicManager::read(uint8_t* out, size_t len) {
    return I2S.readBytes((char*)out, len);
}
