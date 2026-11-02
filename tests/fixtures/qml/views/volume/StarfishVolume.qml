// Синтетический фрагмент в форме, на которую рассчитаны замены trk501 (настоящий файл - в прошивке ТВ).
import QtQuick 2.4

Item {
    id: root
    property bool exterior: LS.audioService.exterior
    property bool syncable: LS.audioService.volumeSyncable

    Image { source: exterior && !syncable ? "arrows.png" : "speaker.png" }
    Text {
        id: number
        visible: !(exterior && !syncable)
        text: LS.audioService.volume + ""
    }
    Rectangle {
        visible: !(exterior && !syncable)
        width: 200 * LS.audioService.volume / 100
    }
}
