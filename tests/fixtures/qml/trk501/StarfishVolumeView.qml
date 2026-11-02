// Синтетический фрагмент в форме, на которую рассчитаны замены trk501 (настоящий файл - в прошивке ТВ).
import QtQuick 2.4

Item {
    id: view
    StarfishVolume { id: volume }

    function updateVisibility() {
        if (!(volume.exterior && (LS.audioService.cause === "volumeUp" || LS.audioService.cause === "volumeDown")))
            volume.shownVolume = LS.audioService.volume;
        visible = true;
        console.info("volume", volume.shownVolume, LS.audioService.cause);
    }
    Connections {
        target: LS.audioService
        onVolumeChanged: updateVisibility()
    }
}
