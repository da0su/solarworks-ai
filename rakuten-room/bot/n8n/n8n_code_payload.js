// ============================================================
// n8n Code node: payload整形（楽天API → 4項目に絞る）
// ============================================================
// 配置: 楽天APIノードの直後
// 目的: 20MB制限対策。不要データを全て捨てて4項目のみにする
// ============================================================

const items = $input.all().slice(0, 10); // 最大10件

return items.map(item => {
  const d = item.json;

  // 楽天API のレスポンス構造に対応
  // Items[].Item.xxx or 直接 xxx
  const src = d.Item || d;

  // 画像URL取得（base64は絶対に使わない）
  let imageUrl = "";
  if (Array.isArray(src.mediumImageUrls) && src.mediumImageUrls.length > 0) {
    // 楽天API形式: mediumImageUrls[0].imageUrl
    const first = src.mediumImageUrls[0];
    imageUrl = (typeof first === "string") ? first : (first.imageUrl || "");
  } else if (Array.isArray(src.smallImageUrls) && src.smallImageUrls.length > 0) {
    const first = src.smallImageUrls[0];
    imageUrl = (typeof first === "string") ? first : (first.imageUrl || "");
  } else {
    imageUrl = src.imageUrl || src.image || "";
  }

  // base64が混入していたら空にする
  imageUrl = String(imageUrl);
  if (imageUrl.startsWith("data:")) {
    imageUrl = "";
  }

  return {
    json: {
      title: String(src.itemName || src.title || "").slice(0, 80),
      url: String(src.itemUrl || src.url || src.product_url || ""),
      image: imageUrl,
      comment: String(src.itemCaption || src.comment || "").slice(0, 400)
    }
  };
});
